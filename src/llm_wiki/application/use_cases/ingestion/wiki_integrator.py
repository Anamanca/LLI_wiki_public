"""Wiki page creation and update logic with vector dedup and snapshots.

Port of the legacy wiki_integrator.py to clean architecture.
Uses ports (LLMClientPort, EmbeddingServicePort, VectorSearchPort) instead of
raw provider functions, and repository abstractions for persistence.

Flow:
  1. Pass 1: Chunked extraction of structured facts from transcript
  2. Vector search existing pages (cosine >= COSINE_THRESHOLD -> update; else -> create)
  3. BEFORE modifying: save page_snapshot
  4. Pass 2: Analyze cause-effect chains and investment implications
  5. Pass 3: Compose final wiki page from extracted facts + analysis
  6. Event extraction, entity linking, stance capture (non-fatal)
  7. Page links, media_asset linking
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from llm_wiki.shared.datetime_utils import now
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select, text, func, delete, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import (
    EmbeddingServicePort,
    LLMClientPort,
)
from llm_wiki.application.use_cases.ingestion.event_extractor import (
    extract_and_store_events,
)
from llm_wiki.application.use_cases.ingestion.event_linker import (
    detect_contradictions,
    link_cause_effect_chains,
)
from llm_wiki.application.ports.repositories.page_repository import (
    PageRepository,
    PageSectionRepository,
)
from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.application.ports.repositories.entity_repository import EntityRepository
from llm_wiki.application.use_cases.ingestion.wiki_prompts import (
    ANALYZE_SYSTEM_PROMPT,
    EXTRACT_SYSTEM_PROMPT,
    WRITE_SYSTEM_PROMPT,
)
from llm_wiki.domain.entities.page import Page, PageLink, PageSection, PageSnapshot
from llm_wiki.domain.entities.event import EventCanonical, EventObservation
from llm_wiki.domain.entities.entity import Entity, EntityRelation, EventEntityLink
from llm_wiki.domain.value_objects.identifiers import (
    EventId,
    PageId,
    SourceId,
    SourceItemId,
)
from llm_wiki.infrastructure.persistence.postgres import models as orm

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (preserved from legacy)
# ---------------------------------------------------------------------------

COSINE_THRESHOLD = 0.82
MAX_MERGE_AGE_DAYS = 30
MIN_ENTITY_JACCARD = 0.3
CHUNK_SIZE = 80_000  # chars per chunk for parallel extraction
OVERLAP = 10_000  # char overlap (12.5% of chunk) to avoid boundary cuts


# ---------------------------------------------------------------------------
# JSON extraction helper (adapted from legacy utils/llm_client.py)
# ---------------------------------------------------------------------------


def extract_json_from_llm_response(content_text: str) -> dict[str, Any]:
    """Parse JSON from LLM response, handling markdown code blocks and leading/trailing noise.

    If the JSON is truncated, attempts to auto-close unmatched braces/brackets.
    """
    text = content_text.strip()
    if text.startswith("```"):
        newline_idx = text.find("\n")
        if newline_idx != -1:
            text = text[newline_idx + 1 :]
        if text.endswith("```"):
            text = text[:-3]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"No JSON object found in response: {text[:200]}")
    json_str = text[start : end + 1]
    try:
        return json.loads(json_str)
    except json.JSONDecodeError:
        repaired = _repair_truncated_json(json_str)
        if repaired is not None:
            logger.warning("Repaired truncated JSON (original error: %s chars)", len(json_str))
            return repaired
        raise ValueError(f"Unrepairable JSON: {json_str[:200]}")


def _repair_truncated_json(json_str: str) -> dict[str, Any] | None:
    """Try to repair a truncated JSON object by auto-closing brackets/braces and removing trailing fragments."""
    stack: list[str] = []
    in_string = False
    escape_next = False
    last_valid_pos = 0

    for i, ch in enumerate(json_str):
        if escape_next:
            escape_next = False
            continue
        if ch == "\\" and in_string:
            escape_next = True
            continue
        if ch == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch in "{[":
            stack.append("}" if ch == "{" else "]")
        elif ch in "}]":
            if stack and ((ch == "}" and stack[-1] == "}") or (ch == "]" and stack[-1] == "]")):
                stack.pop()
                if not stack:
                    last_valid_pos = i + 1

    if not stack:
        return None

    repaired = json_str[:last_valid_pos] if last_valid_pos > 0 else json_str
    for cut_char in [",", ":", "{", "["]:
        pos = repaired.rfind(cut_char)
        if pos > 0:
            repaired = repaired[:pos]
            break

    while stack:
        repaired += stack.pop()

    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Slugify
# ---------------------------------------------------------------------------


def _slugify(title: str) -> str:
    """Create a URL-friendly slug from a title."""
    slug = title.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug[:500]


# ---------------------------------------------------------------------------
# Entity aliases (inlined from legacy entity_aliases.py)
# ---------------------------------------------------------------------------

CANONICAL_ALIASES: dict[str, str] = {
    "nhnn": "ngan_hang_nha_nuoc_viet_nam",
    "ngan hang nha nuoc": "ngan_hang_nha_nuoc_viet_nam",
    "ngan hang nha nuoc viet nam": "ngan_hang_nha_nuoc_viet_nam",
    "sbv": "ngan_hang_nha_nuoc_viet_nam",
    "state bank of vietnam": "ngan_hang_nha_nuoc_viet_nam",
    "uy ban chung khoan": "uy_ban_chung_khoan_nha_nuoc",
    "uy ban chung khoan nha nuoc": "uy_ban_chung_khoan_nha_nuoc",
    "ubck": "uy_ban_chung_khoan_nha_nuoc",
    "ssc": "uy_ban_chung_khoan_nha_nuoc",
    "bo tai chinh": "bo_tai_chinh",
    "mof": "bo_tai_chinh",
    "tong cuc thong ke": "tong_cuc_thong_ke",
    "gso": "tong_cuc_thong_ke",
    "fed": "federal_reserve",
    "cuc du tru lien bang my": "federal_reserve",
    "cuc du tru lien bang": "federal_reserve",
    "federal reserve": "federal_reserve",
    "ecb": "european_central_bank",
    "ngan hang trung uong chau au": "european_central_bank",
    "imf": "international_monetary_fund",
    "quy tien te quoc te": "international_monetary_fund",
    "wb": "world_bank",
    "ngan hang the gioi": "world_bank",
    "world bank": "world_bank",
    "opec": "opec",
    "vn-index": "vn_index",
    "vnindex": "vn_index",
    "vn index": "vn_index",
    "hnx-index": "hnx_index",
    "hnxindex": "hnx_index",
    "s&p 500": "sp500",
    "s&p500": "sp500",
    "dow jones": "dow_jones",
    "nasdaq": "nasdaq",
    "nikkei": "nikkei_225",
    "shanghai composite": "shanghai_composite",
    "hang seng": "hang_seng",
    "ck": "chung_khoan",
    "bds": "bat_dong_san",
    "nh": "ngan_hang",
    "ctck": "cong_ty_chung_khoan",
    "tckh": "thi_truong_chung_khoan",
}


def _canonicalize(name: str) -> str:
    """Return canonical form of entity name, or original if no mapping."""
    key = name.lower().strip()
    key = key.replace(".", "").replace(",", "")
    return CANONICAL_ALIASES.get(key, key)


# ---------------------------------------------------------------------------
# Transcript preprocessing (inlined from legacy transcript_preprocessor.py)
# ---------------------------------------------------------------------------

VI_FILLERS = r"\b(à|ừm|nhỉ|nhé|nha|ờ|hả|nè|nhá|đúng không|phải không)\b"
EN_FILLERS = r"\b(um|uh|like|you know|I mean|sort of|kind of|basically|actually|literally|right|okay|so)\b"
COMBINED_FILLERS = (
    r"\b(à|ừm|nhỉ|nhé|nha|um|uh|like|you know|I mean|sort of|kind of|basically|actually|literally)\b"
)


def _preprocess_transcript(transcript_text: str, lang: str | None = None) -> str:
    """Remove filler words, normalize whitespace, dedup adjacent sentences."""
    if lang == "vi":
        pattern = VI_FILLERS
    elif lang == "en":
        pattern = EN_FILLERS
    else:
        pattern = COMBINED_FILLERS

    text = re.sub(pattern, "", transcript_text, flags=re.IGNORECASE)
    text = re.sub(r"\s+", " ", text).strip()

    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    if not sentences:
        return text

    deduped = [sentences[0]]
    for i in range(1, len(sentences)):
        prev = sentences[i - 1]
        curr = sentences[i]
        similarity = _char_overlap(prev, curr)
        if similarity < 0.8:
            deduped.append(curr)

    return " ".join(deduped)


def _char_overlap(a: str, b: str) -> float:
    """Simple character-level overlap ratio between two strings."""
    if not a or not b:
        return 0.0
    set_a = set(a)
    set_b = set(b)
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    return len(intersection) / min(len(set_a), len(set_b))


# ---------------------------------------------------------------------------
# Chunking & Merging Helpers
# ---------------------------------------------------------------------------


def _chunk_transcript(
    text: str, chunk_size: int = CHUNK_SIZE, overlap: int = OVERLAP
) -> list[str]:
    """Split transcript into overlapping chunks for parallel processing."""
    if len(text) <= chunk_size:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunks.append(text[start:end])
        start += chunk_size - overlap
    return chunks


def _merge_extracted_facts(facts_list: list[dict[str, Any]]) -> dict[str, Any]:
    """Merge extracted facts from multiple chunks, deduplicating entities and events.

    Returns a single facts dict with merged entities, numbers, events, relationships,
    concatenated market_context + chunk_summaries, and best classification from all chunks.
    """
    if not facts_list:
        return {}
    if len(facts_list) == 1:
        return facts_list[0]

    # Classification merging (pick best from first chunk, union key_entities/subtopics)
    merged_classification: dict[str, Any] | None = None
    all_key_entities: set[str] = set()
    all_subtopics: set[str] = set()
    all_existing_pages: set[str] = set()
    domain_votes: dict[str, int] = {}
    lang_votes: dict[str, int] = {}

    for facts in facts_list:
        if not facts:
            continue
        cls = facts.get("classification", {})
        if cls and isinstance(cls, dict):
            if merged_classification is None:
                merged_classification = cls
            for ke in cls.get("key_entities", []) or []:
                if isinstance(ke, dict):
                    name = (ke.get("name") or "").strip()
                    etype = (ke.get("type") or "other").strip()
                    if name:
                        all_key_entities.add(f"{name}::{etype}")
                elif isinstance(ke, str) and ke.strip():
                    all_key_entities.add(f"{ke.strip()}::other")
            for st in cls.get("subtopics", []) or []:
                if st and isinstance(st, str):
                    all_subtopics.add(st.strip())
            for ep in cls.get("existing_pages_to_update", []) or []:
                if ep and isinstance(ep, str):
                    all_existing_pages.add(ep.strip())
            dom = (cls.get("domain") or "").strip()
            if dom:
                domain_votes[dom] = domain_votes.get(dom, 0) + 1
            lang = (cls.get("language") or "").strip()
            if lang:
                lang_votes[lang] = lang_votes.get(lang, 0) + 1

    if merged_classification:
        merged_classification["key_entities"] = [
            {"name": k.split("::")[0], "type": k.split("::")[1] if "::" in k else "other"}
            for k in sorted(all_key_entities)
        ]
        merged_classification["subtopics"] = sorted(all_subtopics)
        merged_classification["existing_pages_to_update"] = sorted(all_existing_pages)
        if domain_votes:
            merged_classification["domain"] = max(domain_votes, key=domain_votes.get)
        if lang_votes:
            merged_classification["language"] = max(lang_votes, key=lang_votes.get)

    merged_entities: dict[str, list[dict]] = {
        "companies": [],
        "people": [],
        "indices": [],
        "policies": [],
    }
    merged_numbers: list[dict] = []
    merged_events: list[dict] = []
    merged_relationships: list[dict] = []
    merged_claims: list[dict] = []
    merged_entity_relations: list[dict] = []
    market_contexts: list[str] = []
    chunk_summaries: list[str] = []
    seen_entity_relation_keys: set[str] = set()
    seen_tickers: set[str] = set()
    seen_people: set[str] = set()
    seen_index_names: set[str] = set()
    seen_policy_names: set[str] = set()
    seen_event_keys: set[tuple] = set()
    seen_relationships: set[str] = set()

    for facts in facts_list:
        if not facts:
            continue
        entities = facts.get("entities", {})
        for c in entities.get("companies", []):
            ticker = ((c.get("ticker") or "").upper() or "").strip()
            name = _canonicalize((c.get("name") or "").lower())
            key = ticker if ticker else name
            if key and key not in seen_tickers:
                seen_tickers.add(key)
                if ticker and not c.get("sector"):
                    for existing in merged_entities["companies"]:
                        if (existing.get("ticker") or "").upper() == ticker and existing.get("sector"):
                            c["sector"] = existing["sector"]
                            break
                merged_entities["companies"].append(c)
        for p in entities.get("people", []):
            name = _canonicalize(p.get("name", "").lower())
            if name and name not in seen_people:
                seen_people.add(name)
                merged_entities["people"].append(p)
        for idx in entities.get("indices", []):
            name = (idx.get("name") or "").lower()
            if name and name not in seen_index_names:
                seen_index_names.add(name)
                merged_entities["indices"].append(idx)
        for pol in entities.get("policies", []):
            name = (pol.get("name") or "").lower()
            if name and name not in seen_policy_names:
                seen_policy_names.add(name)
                merged_entities["policies"].append(pol)

        merged_numbers.extend(facts.get("numbers", []))

        for ev in facts.get("events", []):
            key = (
                ev.get("normalized_date") or "",
                ev.get("category") or "",
                (ev.get("description") or "")[:60].lower(),
            )
            if key not in seen_event_keys:
                seen_event_keys.add(key)
                merged_events.append(ev)

        for rel in facts.get("relationships", []):
            key = f"{rel.get('source','')}|{rel.get('target','')}|{rel.get('relation_type','')}".lower()
            if key not in seen_relationships:
                seen_relationships.add(key)
                merged_relationships.append(rel)

        merged_claims.extend(facts.get("key_claims", []))

        for rel in facts.get("entity_relations", []) or []:
            if not isinstance(rel, dict):
                continue
            key = f"{rel.get('from','')}|{rel.get('to','')}|{rel.get('predicate','')}".lower()
            if key not in seen_entity_relation_keys:
                seen_entity_relation_keys.add(key)
                merged_entity_relations.append(rel)

        ctx = facts.get("market_context", "")
        if ctx:
            market_contexts.append(ctx)
        cs = facts.get("chunk_summary", "")
        if cs:
            chunk_summaries.append(cs)

    return {
        "entities": merged_entities,
        "numbers": merged_numbers,
        "events": merged_events,
        "relationships": merged_relationships,
        "entity_relations": merged_entity_relations,
        "key_claims": merged_claims,
        "market_context": " | ".join(market_contexts) if market_contexts else "",
        "chunk_summaries": chunk_summaries,
        "classification": merged_classification,
    }


# ---------------------------------------------------------------------------
# Multi-Pass LLM Pipeline: Extract -> Analyze -> Write
# ---------------------------------------------------------------------------


def _content_from_raw(raw_resp: dict[str, Any]) -> str:
    """Extract content from raw chat completion response, falling back to reasoning_content."""
    if not isinstance(raw_resp, dict):
        return str(raw_resp)
    choices = raw_resp.get("choices", [{}])
    if not choices:
        return ""
    message = choices[0].get("message", {})
    return message.get("content") or message.get("reasoning_content") or ""


async def _call_llm_json(
    llm: LLMClientPort,
    system_prompt: str,
    user_content: str,
    timeout: float = 300.0,
    temperature: float = 0.2,
    max_retries: int = 3,
    pass_label: str = "LLM",
    max_tokens: int = 16384,
) -> dict[str, Any]:
    """Generic helper: call LLM via port, extract JSON, with retry on parse failure.

    Uses ``chat_completion_raw`` so reasoning_content fallback and token usage are
    preserved through the LLM port / traced wrapper.
    """
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        raw_resp = await asyncio.wait_for(
            llm.chat_completion_raw(messages=messages, temperature=temperature, max_tokens=max_tokens),
            timeout=timeout,
        )
    except asyncio.TimeoutError:
        logger.warning("%s: LLM call timed out after %.1fs", pass_label, timeout)
        raise

    content = _content_from_raw(raw_resp)
    if not content.strip():
        raise ValueError(f"{pass_label}: empty response from LLM")

    try:
        return await asyncio.to_thread(extract_json_from_llm_response, content)
    except (ValueError, json.JSONDecodeError) as parse_err:
        logger.warning("%s: JSON parse failed (%s), retrying...", pass_label, str(parse_err)[:120])
        retry_messages = [
            {
                "role": "system",
                "content": system_prompt
                + "\n\nOUTPUT ONLY VALID COMPLETE JSON. NO TRUNCATION. CLOSE ALL BRACKETS.",
            },
            {"role": "user", "content": user_content[:6000]},
        ]
        try:
            raw_resp2 = await asyncio.wait_for(
                llm.chat_completion_raw(messages=retry_messages, temperature=0.1, max_tokens=max_tokens),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("%s: retry timed out", pass_label)
            raise
        content2 = _content_from_raw(raw_resp2)
        if not content2.strip():
            raise ValueError(f"{pass_label}: empty response from LLM on retry")
        return await asyncio.to_thread(extract_json_from_llm_response, content2)


async def _pass_extract(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    timeout: float,
    published_at: datetime | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Pass 1: Extract structured facts from transcript."""
    domain_info = ""
    if classification.get("domain"):
        domain_info = f"- Domain: {classification['domain']}\n"
    entities_info = ""
    if classification.get("key_entities"):
        ke_list = classification["key_entities"]
        if isinstance(ke_list, list):
            names = []
            for ke in ke_list:
                if isinstance(ke, dict) and ke.get("name"):
                    names.append(ke["name"])
                elif isinstance(ke, str):
                    names.append(ke)
            if names:
                entities_info = f"- Thuc the chinh: {', '.join(names)}\n"

    t0_instruction = ""
    if published_at:
        t0_str = published_at.strftime("%Y-%m-%d")
        t0_instruction = (
            f"NGAY PHAT HANH VIDEO (T0): {t0_str}\n"
            f"QUY TAC TEMPORAL: Quy doi moi moc thoi gian tuong doi sang ngay tuyet doi (ISO YYYY-MM-DD).\n"
            f"Neu khong the quy doi -> de normalized_date = null.\n\n"
        )

    user_content = (
        t0_instruction
        + f"Phan loai:\n- Chu de: {classification.get('main_topic', '')}\n{domain_info}{entities_info}"
        f"- Chu de phu: {', '.join(classification.get('subtopics', []))}\n"
        f"- Ngon ngu: {classification.get('language', 'vi')}\n\n"
        f"Transcript:\n{transcript_text[:100_000]}"
    )
    logger.info("Pass 1/3: Extracting structured facts (%d chars)", len(transcript_text))
    return await _call_llm_json(
        llm,
        EXTRACT_SYSTEM_PROMPT,
        user_content,
        timeout=timeout,
        temperature=temperature,
        pass_label="Pass1-Extract",
    )


async def _pass_extract_chunked(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    timeout: float,
    published_at: datetime | None = None,
    temperature: float = 0.0,
) -> dict[str, Any]:
    """Pass 1 (chunked): Split transcript, extract facts sequentially with sliding context, merge.

    Splits transcript into overlapping chunks, runs Pass 1 on each chunk
    sequentially with context from previous chunk summary, then merges results with dedup.
    Falls back to single-pass if transcript is small.
    """
    chunks = _chunk_transcript(transcript_text)

    if len(chunks) <= 1:
        return await _pass_extract(
            llm,
            transcript_text,
            classification,
            timeout,
            published_at=published_at,
            temperature=temperature,
        )

    logger.info(
        "Pass 1/3: Chunked extraction - %d chunks of ~%d chars each",
        len(chunks),
        CHUNK_SIZE,
    )
    per_chunk_timeout = max(timeout, timeout * len(chunks) * 0.5)

    domain_info = ""
    if classification.get("domain"):
        domain_info = f"- Domain: {classification['domain']}\n"
    entities_info = ""
    if classification.get("key_entities"):
        ke_list = classification["key_entities"]
        if isinstance(ke_list, list):
            names = []
            for ke in ke_list:
                if isinstance(ke, dict) and ke.get("name"):
                    names.append(ke["name"])
                elif isinstance(ke, str):
                    names.append(ke)
            if names:
                entities_info = f"- Thuc the chinh: {', '.join(names)}\n"

    t0_instruction = ""
    if published_at:
        t0_str = published_at.strftime("%Y-%m-%d")
        t0_instruction = (
            f"NGAY PHAT HANH VIDEO (T0): {t0_str}\n"
            f"QUY TAC TEMPORAL: Quy doi moi moc thoi gian tuong doi sang ngay tuyet doi (ISO YYYY-MM-DD).\n"
            f"- 'hom nay' -> {t0_str}\n"
            f"- 'hom qua' -> ngay truoc {t0_str}\n"
            f"- 'tuan truoc' -> {t0_str} tru 7 ngay\n"
            f"- 'thang nay' -> {t0_str[:7]}-01 (giu thang, ngay 01 neu khong ro)\n"
            f"- 'quy X' -> uoc tinh tu T0\n"
            f"Neu khong the quy doi chinh xac -> de normalized_date = null.\n\n"
        )

    async def _extract_chunk(chunk: str, idx: int, prev_summary: str = "") -> dict[str, Any]:
        context_prefix = ""
        if prev_summary:
            context_prefix = (
                f"[NGU CANH TU DOAN TRUOC]\n"
                f"Tom tat doan {idx}: {prev_summary[:500]}\n"
                f"Dung ngu canh nay de giai quyet coreference (VD: 'ong ay', 'chi so nay' -> xac dinh tu context).\n"
                f"[/NGU CANH]\n\n"
            )

        user_content = (
            t0_instruction
            + context_prefix
            + f"[DOAN {idx + 1}/{len(chunks)}]\n"
            f"Phan loai:\n- Chu de: {classification.get('main_topic', '')}\n{domain_info}{entities_info}"
            f"- Chu de phu: {', '.join(classification.get('subtopics', []))}\n"
            f"- Ngon ngu: {classification.get('language', 'vi')}\n\n"
            f"Transcript (doan {idx + 1}):\n{chunk}"
        )
        try:
            return await _call_llm_json(
                llm,
                EXTRACT_SYSTEM_PROMPT,
                user_content,
                timeout=per_chunk_timeout,
                temperature=temperature,
                pass_label=f"Pass1-Extract-Chunk{idx + 1}",
            )
        except Exception as exc:
            logger.warning("Pass 1 chunk %d failed: %s", idx + 1, exc)
            return {}

    facts_list: list[dict[str, Any]] = []
    prev_summary = ""
    for i, chunk in enumerate(chunks):
        result = await _extract_chunk(chunk, i, prev_summary)
        if result:
            facts_list.append(result)
            prev_summary = result.get("chunk_summary", "")
        else:
            facts_list.append({})

    merged = _merge_extracted_facts(facts_list)
    logger.info(
        "Pass 1 chunked OK: merged %d companies, %d people, %d numbers, %d events, %d relationships, %d entity_relations from %d/%d chunks",
        len(merged.get("entities", {}).get("companies", [])),
        len(merged.get("entities", {}).get("people", [])),
        len(merged.get("numbers", [])),
        len(merged.get("events", [])),
        len(merged.get("relationships", [])),
        len(merged.get("entity_relations", []) or []),
        len(facts_list),
        len(chunks),
    )
    return merged


def _build_chunk_context(facts: dict[str, Any], max_summary_chars: int = 60_000) -> str:
    """Build a compact context string from chunk summaries for Pass 2,3."""
    summaries = facts.get("chunk_summaries", [])
    if not summaries:
        return ""

    parts: list[str] = []
    total = 0
    for i, s in enumerate(summaries):
        if total + len(s) > max_summary_chars:
            parts.append(f"[... con {len(summaries) - i} doan nua - da tom luoc]")
            break
        parts.append(f"=== DOAN {i + 1} ===\n{s}")
        total += len(s)

    return "\n\n".join(parts)


async def _pass_analyze(
    llm: LLMClientPort,
    transcript_text: str,
    facts: dict[str, Any],
    classification: dict[str, Any],
    timeout: float,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    """Pass 2: Analyze cause-effect chains and investment implications."""
    facts_json = json.dumps(facts, ensure_ascii=False, indent=2)

    chunk_context = _build_chunk_context(facts)
    if chunk_context:
        transcript_section = f"TOM TAT TOAN BO TRANSCRIPT (tu phan tich tung doan):\n{chunk_context}"
    else:
        transcript_section = f"TRANSCRIPT GOC (de tham khao them ngu canh):\n{transcript_text[:30_000]}"

    t0_prefix = ""
    if published_at:
        t0_prefix = f"Video duoc phat hanh ngay: {published_at.strftime('%Y-%m-%d')}\n\n"

    user_content = (
        f"{t0_prefix}"
        f"Chu de: {classification.get('main_topic', '')}\n"
        f"Ngon ngu: {classification.get('language', 'vi')}\n\n"
        f"DU KIEN DA TRICH XUAT TU TRANSCRIPT:\n{facts_json}\n\n"
        f"{transcript_section}"
    )
    logger.info("Pass 2/3: Analyzing cause-effect & implications (context: %d chars)", len(user_content))
    return await _call_llm_json(
        llm,
        ANALYZE_SYSTEM_PROMPT,
        user_content,
        timeout=timeout,
        pass_label="Pass2-Analyze",
    )


def _build_page_overview(markdown_content: str) -> str:
    """Build a structured overview of ALL sections: title + first sentence.

    Instead of raw truncation which leaves the LLM blind to ~70% of a large page,
    this gives the LLM a complete structural map so it can avoid redundant sections.
    """
    import re

    # Split on ## Section headers
    sections = re.split(r"\n(?=## )", markdown_content)
    overview_lines = []
    for i, section in enumerate(sections):
        section = section.strip()
        if not section:
            continue
        # Extract header line
        header_match = re.match(r"^## (.+)$", section, re.MULTILINE)
        if not header_match:
            continue
        title = header_match.group(1).strip()
        # Extract body (everything after the header line)
        body_start = header_match.end()
        body = section[body_start:].strip()
        # First sentence: up to the first period, newline, or 150 chars
        first_sentence = ""
        for ch in body:
            first_sentence += ch
            if len(first_sentence) > 150 or (ch in ".!?\n" and len(first_sentence) > 20):
                break
        first_sentence = first_sentence.strip()
        overview_lines.append(f"- [{i}] **{title}**: {first_sentence}")

    if not overview_lines:
        # Fallback: truncate raw content at 8000 chars
        return markdown_content[:8000]

    return f"Tong so section: {len(overview_lines)}\n\n" + "\n".join(overview_lines)


async def _pass_write(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    facts: dict[str, Any],
    analysis: dict[str, Any],
    existing_page_content: str | None,
    frame_urls: list[dict] | None,
    timeout: float,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    """Pass 3: Compose final wiki page from extracted facts + analysis."""
    domain_info = ""
    if classification.get("domain"):
        domain_info = f"- Domain: {classification['domain']}\n"
    entities_info = ""
    if classification.get("key_entities"):
        ke_list = classification["key_entities"]
        if isinstance(ke_list, list):
            names = []
            for ke in ke_list:
                if isinstance(ke, dict) and ke.get("name"):
                    names.append(ke["name"])
                elif isinstance(ke, str):
                    names.append(ke)
            if names:
                entities_info = f"- Thuc the chinh: {', '.join(names)}\n"

    facts_json = json.dumps(facts, ensure_ascii=False, indent=2)
    analysis_json = json.dumps(analysis, ensure_ascii=False, indent=2)

    chunk_context = _build_chunk_context(facts)
    if chunk_context:
        transcript_section = f"TOM TAT TOAN BO TRANSCRIPT (day du cac doan):\n{chunk_context}"
    else:
        transcript_section = f"Transcript goc (de kiem tra cheo):\n{transcript_text[:30_000]}"

    t0_prefix = ""
    if published_at:
        t0_prefix = f"Video phat hanh ngay: {published_at.strftime('%Y-%m-%d')}\n\n"

    user_parts = [
        f"{t0_prefix}Phan loai:\n- Chu de: {classification.get('main_topic', '')}\n{domain_info}{entities_info}"
        f"- Chu de phu: {', '.join(classification.get('subtopics', []))}\n"
        f"- Ngon ngu: {classification.get('language', 'vi')}\n"
        f"- Tom tat: {classification.get('summary_3sentences', '')}",
        f"DU KIEN TRICH XUAT:\n{facts_json}",
        f"PHAN TICH CHUYEN SAU:\n{analysis_json}",
        transcript_section,
    ]

    if frame_urls:
        frame_info = "KHUNG HINH CO SAN (co the chen vao noi dung neu lien quan):\n"
        for f in frame_urls:
            frame_info += f"- [{f.get('second')}s] {f.get('description', '')}\n  URL: {f.get('url', '')}\n"
        user_parts.insert(1, frame_info)

    if existing_page_content:
        # Build structured overview: all section titles + first sentence of each.
        # Raw truncation at 8000 chars makes the LLM blind to ~70% of existing content
        # on large pages. A structured summary lets the LLM see ALL sections.
        overview = _build_page_overview(existing_page_content)
        user_parts.insert(
            0,
            f"**CHE DO: CAP NHAT TRANG HIEN CO**\n"
            f"Trang wiki da co {overview.count('[')} section. Day la danh sach TAT CA section da co:\n\n"
            f"{overview}\n\n"
            f"**QUAN TRONG:** Chi viet CAC SECTION MOI, khong co trong danh sach tren.\n"
            f"KHONG viet lai, copy, hay tom tat cac section da co.\n"
            f"Moi section moi phai bo sung goc nhin hoac du lieu CHUA CO trong trang hien tai.\n"
            f"Neu transcript khong co gi moi, tra ve sections = [].",
        )

    user_content = "\n\n---\n\n".join(user_parts)
    logger.info("Pass 3/3: Composing final wiki page (context: %d chars)", len(user_content))
    return await _call_llm_json(
        llm,
        WRITE_SYSTEM_PROMPT,
        user_content,
        temperature=0.3,
        timeout=timeout,
        pass_label="Pass3-Write",
        max_tokens=32768,
    )


# ---------------------------------------------------------------------------
# Vector search for existing pages (raw SQLAlchemy — no suitable repository method)
# ---------------------------------------------------------------------------


async def _vector_search_existing_pages(
    vector: list[float],
    db: AsyncSession,
    limit: int = 5,
) -> list[tuple[orm.Page, float]]:
    """Search existing pages by cosine similarity to the summary vector."""
    if not vector:
        return []

    vec_literal = f"ARRAY{vector}::vector"
    stmt = (
        select(
            orm.Page,
            (
                1.0
                - func.cosine_distance(
                    orm.Page.summary_vector,
                    text(vec_literal),
                )
            ).label("similarity"),
        )
        .where(orm.Page.summary_vector.isnot(None))
        .where(
            (
                1.0
                - func.cosine_distance(
                    orm.Page.summary_vector,
                    text(vec_literal),
                )
            )
            >= COSINE_THRESHOLD
        )
        .order_by(text("similarity DESC"))
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.all()
    matches = [(row[0], float(row[1])) for row in rows if row[0] is not None]
    logger.debug(
        "Vector search found %d matches above threshold %.2f", len(matches), COSINE_THRESHOLD
    )
    return matches


# ---------------------------------------------------------------------------
# Multi-criteria merge decision
# ---------------------------------------------------------------------------


def _should_merge(
    new_published_at: datetime | None,
    existing_page: orm.Page,
    new_entities: list[str],
    similarity: float,
) -> tuple[bool, str]:
    """Decide whether a new video should UPDATE an existing page or CREATE a new one.

    Replaces the single cosine-threshold decision with a multi-gate system:
      GATE 1: Cosine similarity >= COSINE_THRESHOLD (0.82)
      GATE 2: Time gap <= MAX_MERGE_AGE_DAYS (30 days)
      GATE 3: Entity Jaccard overlap >= MIN_ENTITY_JACCARD (0.3)

    Returns (should_merge: bool, reason: str).
    """
    # --- GATE 1: Cosine similarity ---
    if similarity < COSINE_THRESHOLD:
        return False, f"cosine too low ({similarity:.3f} < {COSINE_THRESHOLD})"

    # --- GATE 2: Time proximity ---
    if new_published_at and existing_page.published_at:
        age_gap_days = abs((new_published_at - existing_page.published_at).days)
        if age_gap_days > MAX_MERGE_AGE_DAYS:
            return False, (
                f"time gap too large ({age_gap_days}d > {MAX_MERGE_AGE_DAYS}d): "
                f"new={new_published_at.date()} vs "
                f"existing={existing_page.published_at.date()}"
            )
    elif new_published_at and not existing_page.published_at:
        # Existing page has no date — still allow merge if cosine is high enough
        logger.debug(
            "Page '%s' has no published_at — skipping temporal gate",
            existing_page.title,
        )

    # --- GATE 3: Entity overlap ---
    existing_entities = existing_page.key_entities or []
    if new_entities and existing_entities:
        new_set = _normalize_entity_set(new_entities)
        existing_set = _normalize_entity_set(existing_entities)
        if new_set and existing_set:
            intersection = new_set & existing_set
            union = new_set | existing_set
            jaccard = len(intersection) / len(union) if union else 0.0
            if jaccard < MIN_ENTITY_JACCARD:
                return False, (
                    f"entity overlap too low (Jaccard={jaccard:.2f} < {MIN_ENTITY_JACCARD}): "
                    f"new={sorted(new_set)[:5]}... vs "
                    f"existing={sorted(existing_set)[:5]}..."
                )

    return True, (
        f"all gates passed: cosine={similarity:.3f}, "
        f"entities={len(new_entities)} new vs {len(existing_entities)} existing"
    )


def _normalize_entity_set(entities: list[str]) -> set[str]:
    """Normalize entity names for comparison: lowercase, strip whitespace, remove type suffix."""
    result: set[str] = set()
    for e in entities:
        if not e:
            continue
        # Strip "::type" suffix if present
        name = e.split("::")[0].strip().lower()
        if name:
            result.add(name)
    return result


# ---------------------------------------------------------------------------
# Snapshot handling (raw SQLAlchemy for upsert semantics)
# ---------------------------------------------------------------------------


async def _save_snapshot(
    page: orm.Page,
    source_item_id: UUID,
    db: AsyncSession,
) -> orm.PageSnapshot | None:
    """Save a snapshot of a page before modification for rollback.

    Uses ON CONFLICT DO NOTHING to safely handle duplicate (page_id, source_item_id)
    from previous failed attempts without rolling back the current transaction.
    """
    sections_result = await db.execute(
        select(
            orm.PageSection.title,
            orm.PageSection.content_markdown,
            orm.PageSection.section_order,
            orm.PageSection.source_ref,
        )
        .where(orm.PageSection.page_id == page.id)
        .order_by(orm.PageSection.section_order)
    )
    sections_data = [
        {"title": r[0], "content_markdown": r[1], "section_order": r[2], "source_ref": r[3]}
        for r in sections_result.all()
    ]

    stmt = (
        pg_insert(orm.PageSnapshot)
        .values(
            page_id=page.id,
            source_item_id=source_item_id,
            content_markdown=page.content_markdown,
            sections_jsonb=sections_data,
        )
        .on_conflict_do_nothing(constraint="uq_page_snapshots_page_item")
    )
    await db.execute(stmt)
    await db.flush()

    result = await db.execute(
        select(orm.PageSnapshot).where(
            orm.PageSnapshot.page_id == page.id,
            orm.PageSnapshot.source_item_id == source_item_id,
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Page create / update helpers
# ---------------------------------------------------------------------------


async def _create_page(
    data: dict[str, Any],
    source_id: UUID,
    source_item_id: UUID,
    summary_vector: list[float],
    published_at: datetime | None,
    db: AsyncSession,
) -> orm.Page:
    """Create a new wiki page from LLM output."""
    slug = data.get("page_slug") or _slugify(data.get("page_title", "untitled"))

    page = orm.Page(
        source_id=source_id,
        source_item_id=source_item_id,
        title=data.get("page_title", "Untitled"),
        slug=slug,
        content_markdown="",  # regenerated from sections below
        summary=data.get("summary", ""),
        summary_vector=summary_vector,
        published_at=published_at,
        status="published",
    )
    db.add(page)
    await db.flush()

    valid_sections = []
    for idx, sec in enumerate(data.get("sections", [])):
        sec_content = sec.get("content_markdown", "")
        # Guard: reject thin/non-substantive section content (< 200 chars).
        if not sec_content or len(sec_content.strip()) < 200:
            logger.warning(
                "Skipping section '%s' in new page: content too short (%d chars, min 200)",
                sec.get("title", ""),
                len(sec_content.strip()),
            )
            continue
        section = orm.PageSection(
            page_id=page.id,
            source_id=source_id,
            section_order=sec.get("order", idx),
            title=sec.get("title", ""),
            content_markdown=sec_content,
            source_ref=sec.get("source_ref", f"yt:{source_item_id}"),
        )
        db.add(section)
        valid_sections.append(section)

    # Regenerate page.content_markdown from all inserted sections.
    if valid_sections:
        page.content_markdown = "\n\n".join(
            f"## {s.title}\n\n{s.content_markdown}" for s in valid_sections
        )

    await db.flush()
    logger.info(
        "Created new page: %s (slug=%s) with %d sections",
        page.title,
        slug,
        len(valid_sections),
    )
    return page


async def _update_page(
    page: orm.Page,
    data: dict[str, Any],
    source_id: UUID,
    source_item_id: UUID,
    db: AsyncSession,
) -> orm.Page:
    """Update an existing wiki page - deduplicate sections from same source, append new ones.

    IMPORTANT: Never blindly overwrite page.content_markdown with the LLM's value.
    The page-level content_markdown is regenerated from ALL sections after the update,
    so stale/corrupted values from the LLM output don't erase existing content.
    """
    # Only update summary from LLM — content_markdown is regenerated from sections below
    if data.get("summary"):
        page.summary = data.get("summary", page.summary)
    page.updated_at = now()

    deleted = await db.execute(
        delete(orm.PageSection).where(
            orm.PageSection.page_id == page.id,
            orm.PageSection.source_ref == f"yt:{source_item_id}",
        )
    )
    old_count = deleted.rowcount or 0
    if old_count:
        logger.debug("Removed %d old sections from source_item %s", old_count, source_item_id)

    order_result = await db.execute(
        select(func.coalesce(func.max(orm.PageSection.section_order), -1)).where(
            orm.PageSection.page_id == page.id
        )
    )
    max_order = order_result.scalar() or 0

    for idx, sec in enumerate(data.get("sections", [])):
        sec_content = sec.get("content_markdown", "")
        # Guard: reject thin/non-substantive section content (< 200 chars).
        # The WRITE prompt requires "TỐI THIỂU 200 từ" per section. Content below
        # this threshold is either truncated, a thin duplicate of an existing
        # section, or an LLM placeholder — not real content.
        if not sec_content or len(sec_content.strip()) < 200:
            logger.warning(
                "Skipping section '%s': content too short (%d chars, min 200)",
                sec.get("title", ""),
                len(sec_content.strip()),
            )
            continue
        section = orm.PageSection(
            page_id=page.id,
            source_id=source_id,
            section_order=max_order + 1,
            title=sec.get("title", ""),
            content_markdown=sec_content,
            source_ref=sec.get("source_ref", f"yt:{source_item_id}"),
        )
        db.add(section)
        max_order += 1

    # Regenerate page.content_markdown from ALL sections (old + new) so the
    # stored value always reflects the real content.
    all_sections_result = await db.execute(
        select(orm.PageSection)
        .where(orm.PageSection.page_id == page.id)
        .order_by(orm.PageSection.section_order)
    )
    all_sections = all_sections_result.scalars().all()
    if all_sections:
        page.content_markdown = "\n\n".join(
            f"## {s.title}\n\n{s.content_markdown}" for s in all_sections
        )

    await db.flush()
    logger.info(
        "Updated page: %s with %d new sections (replaced %d old)",
        page.title,
        len(data.get("sections", [])),
        old_count,
    )
    return page


# ---------------------------------------------------------------------------
# Run passes helpers
# ---------------------------------------------------------------------------


async def _run_extraction_pass(
    llm: LLMClientPort,
    transcript_text: str,
    classification_hint: dict[str, Any] | None = None,
    published_at: datetime | None = None,
    timeout: float = 300.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Pass 1 only: chunked extraction -> facts + merged classification.

    Args:
        llm: LLM client port.
        transcript_text: Raw transcript text.
        classification_hint: Optional classification dict to guide extraction.
            If None, creates empty hint for Pass 1 to self-classify.
        timeout: Timeout per chunk API call.

    Returns:
        Tuple of (facts_dict, merged_classification).
        Raises RuntimeError if classification is completely empty.
    """
    if classification_hint is None:
        classification_hint = {
            "main_topic": "",
            "domain": "",
            "subtopics": [],
            "key_entities": [],
            "language": "vi",
            "summary_3sentences": "",
        }

    lang = classification_hint.get("language")
    cleaned = _preprocess_transcript(transcript_text, lang=lang)

    facts = await _pass_extract_chunked(
        llm,
        cleaned,
        classification_hint,
        timeout,
        published_at=published_at,
    )
    logger.info(
        "Pass 1 OK: %d companies, %d people, %d numbers, %d events, %d relationships, %d entity_relations, %d chunk summaries",
        len(facts.get("entities", {}).get("companies", [])),
        len(facts.get("entities", {}).get("people", [])),
        len(facts.get("numbers", [])),
        len(facts.get("events", [])),
        len(facts.get("relationships", [])),
        len(facts.get("entity_relations", []) or []),
        len(facts.get("chunk_summaries", [])),
    )

    merged_classification = dict(classification_hint)
    if facts.get("classification"):
        try:
            cls_data = facts["classification"]
            merged_classification = {
                "main_topic": cls_data.get("main_topic", classification_hint.get("main_topic", "")),
                "domain": cls_data.get("domain", classification_hint.get("domain", "")),
                "subtopics": cls_data.get("subtopics", classification_hint.get("subtopics", [])),
                "key_entities": cls_data.get("key_entities", classification_hint.get("key_entities", [])),
                "language": cls_data.get("language", classification_hint.get("language", "vi")),
                "summary_3sentences": cls_data.get(
                    "summary_3sentences", classification_hint.get("summary_3sentences", "")
                ),
                "existing_pages_to_update": cls_data.get(
                    "existing_pages_to_update", classification_hint.get("existing_pages_to_update", [])
                ),
            }
            logger.info("Using classification from Pass 1 (merged)")
        except Exception as exc:
            logger.warning("Failed to build classification from facts: %s - using hint", exc)

    if not merged_classification.get("main_topic"):
        logger.warning("Pass 1 produced empty classification - caller should use cold fallback")

    return facts, merged_classification


async def _run_synthesis_passes(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    facts: dict[str, Any],
    existing_page_content: str | None = None,
    frame_urls: list[dict] | None = None,
    timeout: float = 450.0,
    published_at: datetime | None = None,
) -> dict[str, Any]:
    """Run Pass 2 (Analyze) + Pass 3 (Write) with existing page context.

    Returns:
        Dict with keys: "wiki", "facts", "analysis", "classification"
    """
    lang = classification.get("language")
    cleaned = _preprocess_transcript(transcript_text, lang=lang)

    pass_timeout = timeout * 0.4

    analysis: dict[str, Any] = {}

    if facts:
        try:
            analysis = await _pass_analyze(
                llm,
                cleaned,
                facts,
                classification,
                pass_timeout,
                published_at=published_at,
            )
            logger.info(
                "Pass 2 OK: %d cause-effect chains, %d investment implications",
                len(analysis.get("cause_effect_chains", [])),
                len(analysis.get("investment_implications", [])),
            )
        except Exception as exc:
            logger.warning("Pass 2 (Analyze) failed: %s - continuing without analysis", exc)
    else:
        logger.info("Pass 2 skipped: no facts from Pass 1")

    try:
        data = await _pass_write(
            llm,
            cleaned,
            classification,
            facts,
            analysis,
            existing_page_content,
            frame_urls,
            timeout=pass_timeout * 2.5,
            published_at=published_at,
        )
        logger.info(
            "Pass 3 OK: wiki page '%s' (%d sections)",
            data.get("page_title", "?"),
            len(data.get("sections", [])),
        )
    except Exception as exc:
        logger.error("Pass 3 (Write) failed: %s", exc)
        raise RuntimeError("Wiki page generation failed: all passes exhausted") from exc

    return {"wiki": data, "facts": facts, "analysis": analysis, "classification": classification}


# ---------------------------------------------------------------------------
# Main WikiIntegrator class
# ---------------------------------------------------------------------------


class WikiIntegrator:
    """Orchestrates the 3-pass LLM pipeline for wiki page creation/update.

    Usage:
        integrator = WikiIntegrator(
            llm=llm_port,
            embedder=embedding_port,
            page_repo=page_repo,
            section_repo=section_repo,
            event_repo=event_repo,
            entity_repo=entity_repo,
        )
        page_ref = await integrator.integrate(
            item=source_item,
            transcript_text=transcript_text,
            classification={"main_topic": "...", "language": "vi", ...},
            db=async_session,
        )
    """

    def __init__(
        self,
        llm: LLMClientPort,
        embedder: EmbeddingServicePort,
        page_repo: PageRepository | None = None,
        section_repo: PageSectionRepository | None = None,
        event_repo: EventRepository | None = None,
        entity_repo: EntityRepository | None = None,
    ):
        self._llm = llm
        self._embedder = embedder
        self._page_repo = page_repo
        self._section_repo = section_repo
        self._event_repo = event_repo
        self._entity_repo = entity_repo

    async def integrate(
        self,
        item: orm.SourceItem,
        transcript_text: str,
        classification: dict[str, Any] | None = None,
        summary_vector: list[float] | None = None,
        db: AsyncSession | None = None,
        source_id: UUID | None = None,
        source_item_id: UUID | None = None,
        published_at: datetime | None = None,
        timeout: float = 300.0,
    ) -> dict[str, Any]:
        """Main integration entry point - split-phase pipeline.

        Args:
            item: SourceItem ORM record (for transcript_text, source_id, published_at, etc.)
            transcript_text: Raw transcript text.
            classification: Optional classification dict with keys: main_topic, domain,
                subtopics, key_entities, language, summary_3sentences.
            summary_vector: Optional pre-computed summary vector. If not provided,
                built from classification text via embedder.
            db: AsyncSession for direct DB operations (vector search, snapshots, page CRUD).
            source_id: Override the source ID (defaults to item.source_id).
            source_item_id: Override the source item ID (defaults to item.id).
            published_at: Override published_at (defaults to item.published_at).

        Returns dict with:
            - "action": "created" | "updated" | "skipped"
            - "page_id": new or updated page UUID string
            - "page_title": title of the page
        """
        source_id_val = source_id or item.source_id
        source_item_id_val = source_item_id or item.id
        published_at_val = published_at or item.published_at

        if db is None:
            raise ValueError("db (AsyncSession) is required for integration")

        # Step 1: Run Pass 1 extraction -> facts + classification (100% coverage)
        facts, effective_classification = await _run_extraction_pass(
            llm=self._llm,
            transcript_text=transcript_text,
            classification_hint=classification,
            published_at=published_at_val,
            timeout=timeout,
        )

        # If Pass 1 failed to classify and caller provided a fallback, use it
        if not effective_classification.get("main_topic") and classification and classification.get("main_topic"):
            logger.warning("Pass 1 classification empty - using provided classification as fallback")
            effective_classification = classification

        if not effective_classification.get("main_topic"):
            raise RuntimeError("Cannot proceed: no classification available from Pass 1 or caller")

        # Step 2: Build summary_vector from merged classification if not provided
        if summary_vector is None or not summary_vector:
            classification_text = (
                effective_classification.get("summary_3sentences")
                or effective_classification.get("main_topic", "")
            )
            if classification_text:
                emb = await self._embedder.embed(classification_text)
                summary_vector = emb.vector
            else:
                raise RuntimeError("Failed to build summary_vector from classification")

        # Step 3: Vector search for existing matching pages
        matches = await _vector_search_existing_pages(summary_vector, db)

        # Step 4: Collect frame URLs from media_assets for LLM image embedding
        assets_result = await db.execute(
            select(orm.MediaAsset).where(orm.MediaAsset.source_item_id == source_item_id_val)
        )
        media_assets = assets_result.scalars().all()
        frame_urls: list[dict] = []
        for asset in media_assets:
            if asset.minio_path and asset.file_size_bytes and asset.file_size_bytes > 0:
                # NOTE: Presigned URL generation requires minio_client which is not
                # yet ported to clean architecture. Build a path-based URL as fallback.
                frame_urls.append({
                    "second": asset.filename.replace("s.jpg", "").replace("s_error.jpg", ""),
                    "url": f"/api/media/{asset.minio_path}",
                    "description": asset.description or "",
                })

        # Step 5: Pass 2 + Pass 3 (with existing page context if match passes multi-criteria gates)
        # Extract entity names from classification for gate 3 (entity Jaccard)
        new_entity_names: list[str] = []
        if effective_classification.get("key_entities"):
            ke_list = effective_classification["key_entities"]
            if isinstance(ke_list, list):
                new_entity_names = [
                    k["name"] if isinstance(k, dict) else str(k)
                    for k in ke_list
                ]

        best_match: orm.Page | None = None
        matched_similarity: float = 0.0
        if matches:
            for candidate, sim in matches:
                should_merge, reason = _should_merge(
                    published_at_val, candidate, new_entity_names, sim,
                )
                if should_merge:
                    best_match = candidate
                    matched_similarity = sim
                    logger.info(
                        "Match accepted for page '%s': %s",
                        best_match.title,
                        reason,
                    )
                    break
                logger.info(
                    "Match rejected for page '%s': %s — trying next candidate",
                    candidate.title,
                    reason,
                )

        if best_match is not None:
            await _save_snapshot(best_match, source_item_id_val, db)
            await db.commit()

            existing_sections_result = await db.execute(
                select(orm.PageSection).where(orm.PageSection.page_id == best_match.id)
            )
            existing_sections = [
                {"title": s.title, "content_markdown": s.content_markdown}
                for s in existing_sections_result.scalars().all()
            ]

            # Build existing_page_content from ALL sections (not page.content_markdown,
            # which can be stale/corrupted when prior updates failed).
            existing_page_content = "\n\n".join(
                f"## {s['title']}\n\n{s['content_markdown']}" for s in existing_sections
            ) if existing_sections else best_match.content_markdown

            llm_result = await _run_synthesis_passes(
                self._llm,
                transcript_text,
                effective_classification,
                facts,
                existing_page_content=existing_page_content,
                frame_urls=frame_urls if frame_urls else None,
                published_at=published_at_val,
            )
            llm_data = llm_result["wiki"]
            analysis = llm_result.get("analysis", {})
            page = await _update_page(best_match, llm_data, source_id_val, source_item_id_val, db)

            if effective_classification.get("domain"):
                page.domain = effective_classification["domain"]
            if new_entity_names:
                page.key_entities = new_entity_names

            action = "updated"
            page_id = str(page.id)
        else:
            logger.info("No matching page passed merge gates - creating new page")
            llm_result = await _run_synthesis_passes(
                self._llm,
                transcript_text,
                effective_classification,
                facts,
                frame_urls=frame_urls if frame_urls else None,
                published_at=published_at_val,
            )
            llm_data = llm_result["wiki"]
            analysis = llm_result.get("analysis", {})
            page = await _create_page(
                llm_data,
                source_id_val,
                source_item_id_val,
                summary_vector,
                published_at_val,
                db,
            )

            if effective_classification.get("domain"):
                page.domain = effective_classification["domain"]
            if new_entity_names:
                page.key_entities = new_entity_names

            stmt = (
                pg_insert(orm.PageSnapshot)
                .values(
                    page_id=page.id,
                    source_item_id=source_item_id_val,
                    content_markdown=None,
                    sections_jsonb=[],
                )
                .on_conflict_do_nothing(constraint="uq_page_snapshots_page_item")
            )
            await db.execute(stmt)
            await db.commit()

            action = "created"
            page_id = str(page.id)

        # Step 6: Event extraction + entity linking + stance (non-fatal)
        try:
            await self._handle_event_extraction(
                facts=facts,
                analysis=analysis,
                source_id_val=source_id_val,
                page_id_val=UUID(page_id),
                published_at_val=published_at_val,
                db=db,
            )
        except Exception as exc:
            logger.warning("Event extraction/linking failed (non-fatal): %s", exc)

        # Step 7: Handle page_links — dedup via ON CONFLICT DO NOTHING
        links = llm_data.get("page_links", [])
        for link_info in links:
            target_slug = link_info.get("slug", "")
            if not target_slug:
                continue
            target_result = await db.execute(
                select(orm.Page).where(orm.Page.slug == target_slug).limit(1)
            )
            target_page = target_result.scalar()
            # Skip self-links
            if not target_page or target_page.id == page.id:
                continue
            relation_type = link_info.get("relation_type", "related")
            # Use INSERT ... ON CONFLICT DO NOTHING to prevent duplicate links
            stmt = (
                pg_insert(orm.PageLink)
                .values(
                    from_page_id=page.id,
                    to_page_id=target_page.id,
                    relation_type=relation_type,
                )
                .on_conflict_do_nothing(constraint="uq_page_links_from_to_relation")
            )
            await db.execute(stmt)

        # Step 8: Link media_assets to the page
        if media_assets:
            for asset in media_assets:
                if asset.page_id is None:
                    asset.page_id = page.id
            await db.flush()
            logger.info("Linked %d media_assets to page %s", len(media_assets), page.title)

        return {
            "action": action,
            "page_id": page_id,
            "page_title": page.title,
        }

    async def _handle_event_extraction(
        self,
        facts: dict[str, Any],
        analysis: dict[str, Any],
        source_id_val: UUID,
        page_id_val: UUID,
        published_at_val: datetime | None,
        db: AsyncSession,
    ) -> None:
        """Extract, deduplicate, and store events; link entities and cause-effect chains.

        Uses the ported legacy event_extractor and event_linker logic.
        """
        events_data = facts.get("events", [])
        entities_data = facts.get("entities", {})
        entity_relations_data = facts.get("entity_relations", [])

        # Run the full event extraction pipeline
        await extract_and_store_events(
            events=events_data,
            source_id=source_id_val,
            page_id=page_id_val,
            source_published_at=published_at_val,
            db=db,
            embedder=self._embedder,
            entities_data=entities_data,
            entity_relations_data=entity_relations_data,
        )

        # Link Pass 2 cause-effect chains
        cause_effect_chains = analysis.get("cause_effect_chains", []) if isinstance(analysis, dict) else []
        await link_cause_effect_chains(
            llm=self._llm,
            embedder=self._embedder,
            cause_effect_chains=cause_effect_chains,
            page_id=page_id_val,
            db=db,
        )

        # Detect contradictions against other sources
        await detect_contradictions(page_id=page_id_val, db=db)

        # Stance capture from analysis
        if analysis and analysis.get("speaker_stance"):
            stance = analysis["speaker_stance"].get("overall_bias")
            if stance:
                sentiment_map = {
                    "bullish": 0.7,
                    "bearish": -0.7,
                    "cautious": -0.1,
                    "neutral": 0.0,
                }
                await db.execute(
                    update(orm.EventObservation)
                    .where(
                        orm.EventObservation.page_id == page_id_val,
                        orm.EventObservation.stance.is_(None),
                    )
                    .values(stance=stance, sentiment_score=sentiment_map.get(stance, 0.0))
                )
                logger.debug("Captured stance '%s' for observations on page %s", stance, page_id_val)
