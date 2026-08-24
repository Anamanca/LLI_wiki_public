"""Wiki page creation and update logic with vector dedup and snapshots.

Port of the legacy wiki_integrator.py to clean architecture.
Uses ports (LLMClientPort, EmbeddingServicePort, VectorSearchPort) instead of
raw provider functions, and repository abstractions for persistence.

Flow:
  1. Pass 1: Single-pass extraction of structured facts from full transcript.
     DeepSeek v4 Flash (1M token context) handles even the longest transcripts
     (~800K chars / ~500K tokens) in one pass — no chunking needed.
  2. Vector search existing pages (cosine >= COSINE_THRESHOLD -> update; else -> create)
  3. BEFORE modifying: save page_snapshot
  4. Pass 2: Analyze+Write combined — composes final wiki page from extracted facts
     (cause-effect chains, investment implications, speaker stance are analyzed
     and embedded in the ### subsections by the LLM with reasoning ON)
  5. Event extraction, entity linking (non-fatal)
  6. Page links
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.entity_repository import EntityRepository
from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.application.ports.repositories.page_repository import (
    PageRepository,
    PageSectionRepository,
)
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
from llm_wiki.application.use_cases.ingestion.wiki_prompts import (
    EXTRACT_SYSTEM_PROMPT,
    REFLECT_SYSTEM_PROMPT,
    WRITE_SYSTEM_PROMPT,
)
from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants (preserved from legacy)
# ---------------------------------------------------------------------------

COSINE_THRESHOLD = 0.82
MAX_MERGE_AGE_DAYS = 30
MIN_ENTITY_JACCARD = 0.3
REQUIRED_SUBSECTIONS = [
    "### Ý chính",
    "### Mốc thời gian & Số liệu",
    "### Tóm tắt chi tiết",
    "### Keywords",
]


def validate_section_structure(content_markdown: str) -> dict:
    """Check a section's content_markdown for required ### subsections.

    Returns:
        {"compliant": bool, "missing": [...], "found": [...], "warnings": [...]}
    """
    found = []
    missing = []
    for sub in REQUIRED_SUBSECTIONS:
        if sub in content_markdown:
            found.append(sub)
        else:
            missing.append(sub)

    warnings = []
    if "### Ý chính" not in content_markdown:
        warnings.append("CRITICAL: missing ### Ý chính (minimum viable subsection)")

    return {
        "compliant": len(missing) == 0,
        "found": found,
        "missing": missing,
        "warnings": warnings,
    }


def _validate_wiki_output(data: dict[str, Any]) -> None:
    """Validate structural compliance of all sections post-WRITE.

    In warn-only mode (default): logs warnings and emits Prometheus metrics.
    In strict mode (WIKI_STRICT_VALIDATION=true): removes non-compliant sections.
    """
    import os

    if not isinstance(data, dict):
        raise ValueError(f"Pass output is not a dict (got {type(data).__name__}) — cannot validate")
    strict_mode = os.getenv("WIKI_STRICT_VALIDATION", "false").lower() == "true"
    sections = data.get("sections", [])
    if not sections:
        return

    compliant_count = 0
    total = 0
    for sec in sections:
        content = sec.get("content_markdown", "")
        if not content:
            continue
        total += 1
        result = validate_section_structure(content)
        if result["compliant"]:
            compliant_count += 1
        else:
            logger.warning(
                "Section '%s' missing subsections: %s (found: %s, warnings: %s)",
                sec.get("title"),
                result["missing"],
                result["found"],
                result["warnings"],
            )
            if strict_mode:
                logger.error(
                    "WIKI_STRICT_VALIDATION: rejecting section '%s' — missing %s",
                    sec.get("title"),
                    result["missing"],
                )
                # Mark for removal (handled by caller filtering non-compliant sections)
                sec["_non_compliant"] = True

    if total > 0:
        ratio = compliant_count / total
        logger.info(
            "Section structure compliance: %d/%d (%.0f%%)",
            compliant_count, total, ratio * 100,
        )
        try:
            from llm_wiki.infrastructure.telemetry.business_metrics import set_gauge
            set_gauge("wiki_section_structure_compliance_ratio", ratio, {"source": "write_pass"})
        except Exception:
            pass  # metrics are best-effort

    if strict_mode:
        data["sections"] = [s for s in sections if not s.get("_non_compliant", False)]
        removed = total - len(data["sections"])
        if removed:
            logger.warning("Strict mode: removed %d non-compliant section(s)", removed)


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
        raise ValueError(f"Unrepairable JSON: {json_str[:200]}") from None


def _repair_truncated_json(json_str: str) -> dict[str, Any] | None:
    """Try to repair truncated JSON by auto-closing brackets/braces and removing fragments."""
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
        elif (
            ch in "}]"
            and stack
            and ((ch == "}" and stack[-1] == "}") or (ch == "]" and stack[-1] == "]"))
        ):
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


_SUBSECTION_PATTERN = re.compile(r"^###\s+.+?\n\n", re.MULTILINE)


def _strip_section_header(content_markdown: str, title: str | None = None) -> str:
    """Strip the leading ``## Title\\n\\n`` from section content, if present.

    The LLM sometimes includes the section header inside ``content_markdown``
    (following the markdown structure shown in the few-shot example).  We strip
    it so that ``_create_page`` / ``_update_page`` do not produce double
    headers when they regenerate ``page.content_markdown`` from all sections.

    Also strips ALL ``### `` subsection headers after the ``## `` header,
    so the 200-char gate counts only prose content.
    """
    text = content_markdown.lstrip()
    m = re.match(r"^##\s+([^\n]+)\n\n", text)
    if not m:
        return text
    stripped_title = m.group(1).strip()
    # If we know the expected title, verify it matches to avoid false positives.
    if title is not None and stripped_title != title.strip():
        return text
    text = text[m.end():]
    # Strip ALL ### subsection header lines — remaining text = prose only for 200-char gate
    text = _SUBSECTION_PATTERN.sub("", text)
    return text


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
EN_FILLERS = (
    r"\b(um|uh|like|you know|I mean|sort of|kind of|basically|actually|literally|right|okay|so)\b"
)
COMBINED_FILLERS = (
    r"\b(à|ừm|nhỉ|nhé|nha|um|uh|like|you know|I mean|sort of|kind of|basically|actually|"
    r"literally)\b"
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


_VI_DIACRITIC_CHARS = set(
    "ăâđêôơưàảãáạằẳẵắặầẩẫấậèẻẽéẹềểễếệìỉĩíịòỏõóọồổỗốộờởỡớợùủũúụừửữứựỳỷỹýỵ"
)


def _detect_transcript_language(text: str) -> str:
    """Deterministically detect dominant transcript language (vi/en/mixed).

    Uses Vietnamese diacritic density — reliable for Vietnamese speech mixed
    with English financial terms (tickers, indices). Overrides LLM language
    drift in classification: flash models tend to mislabel Vietnamese
    transcripts as English.
    """
    if not text:
        return "vi"
    alpha = sum(1 for c in text if c.isalpha())
    if alpha == 0:
        return "vi"
    vi_ratio = sum(1 for c in text if c in _VI_DIACRITIC_CHARS) / alpha
    if vi_ratio >= 0.02:
        return "vi"
    if vi_ratio <= 0.001:
        return "en"
    return "mixed"


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
    enable_thinking: bool = False,
    allow_retry: bool = True,
) -> dict[str, Any]:
    """Generic helper: call LLM via port, extract JSON, with retry on parse failure.

    Uses ``chat_completion_raw`` so reasoning_content fallback and token usage are
    preserved through the LLM port / traced wrapper.

    ``enable_thinking`` defaults to ``False`` for JSON extraction — reasoning
    burns token budget on hidden chain-of-thought, truncating the JSON output.
    Pass 2 (write) may enable thinking via ``settings.wiki_write_thinking_enabled``.
    """
    from llm_wiki.infrastructure.telemetry.business_metrics import inc_counter

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_content},
    ]
    try:
        raw_resp = await asyncio.wait_for(
            llm.chat_completion_raw(
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                enable_thinking=enable_thinking,
            ),
            timeout=timeout,
        )
    except TimeoutError:
        inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "error"})
        logger.warning("%s: LLM call timed out after %.1fs", pass_label, timeout)
        raise

    content = _content_from_raw(raw_resp)
    if not content.strip():
        inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "error"})
        raise ValueError(f"{pass_label}: empty response from LLM")

    try:
        parsed = await asyncio.to_thread(extract_json_from_llm_response, content)
        if not isinstance(parsed, dict):
            raise ValueError(f"{pass_label}: JSON extractor returned non-dict ({type(parsed).__name__})")
        inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "ok"})
        return parsed
    except (ValueError, json.JSONDecodeError) as parse_err:
        if not allow_retry:
            raise
        inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "parse_retry"})
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
                llm.chat_completion_raw(
                    messages=retry_messages,
                    temperature=0.1,
                    max_tokens=max_tokens,
                    enable_thinking=enable_thinking,
                ),
                timeout=timeout,
            )
        except TimeoutError:
            logger.warning("%s: retry timed out", pass_label)
            raise
        content2 = _content_from_raw(raw_resp2)
        if not content2.strip():
            raise ValueError(f"{pass_label}: empty response from LLM on retry") from None
        try:
            parsed2 = await asyncio.to_thread(extract_json_from_llm_response, content2)
        except (ValueError, json.JSONDecodeError) as parse_err2:
            inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "error"})
            raise ValueError(
                f"{pass_label}: retry JSON unparseable: {str(parse_err2)[:120]}"
            ) from None
        inc_counter("llm_pass_calls_total", {"pass": pass_label, "status": "ok"})
        return parsed2
async def _pass_extract(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    timeout: float,
    published_at: datetime | None = None,
    temperature: float = 0.0,
    chunk_range: tuple[float, float] | None = None,
    video_id: str | None = None,
) -> dict[str, Any]:
    """Pass 1: Extract structured facts from transcript.

    ``chunk_range`` scopes extraction to a timestamp window (map step of the
    chunked pipeline); ``video_id`` is included so facts can cite the source.
    """
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
            "QUY TAC TEMPORAL: Quy doi moi moc thoi gian tuong doi sang ngay tuyet doi"
            f" (ISO YYYY-MM-DD).\n"
            "Neu khong the quy doi -> de normalized_date = null.\n\n"
        )

    scope_instruction = ""
    if chunk_range is not None:
        scope_instruction = (
            f"PHAM VI TRANSCRIPT NAY: {chunk_range[0]:.0f}s - {chunk_range[1]:.0f}s\n"
            "CHI trich xuat cac phat bieu co noi dung trong pham vi nay.\n"
            "Neu phat bieu tham chieu noi dung ngoai pham vi, van ghi phat bieu hien tai "
            "nhung KHONG tu dien du kien bi thieu; them context_dependency=true va "
            "reference_start_time neu xac dinh duoc.\n"
            "start_time la thoi diem phat bieu hien tai, khong phai thoi diem tham chieu.\n\n"
        )
    video_info = ""
    if video_id:
        video_info = f"VIDEO ID: {video_id}\n"

    user_content = (
        video_info
        + t0_instruction
        + scope_instruction
        + f"Phan loai:\n- Chu de: {classification.get('main_topic', '')}\n"
        f"{domain_info}{entities_info}"
        f"- Chu de phu: {', '.join(classification.get('subtopics', []))}\n"
        f"- Ngon ngu: {classification.get('language', 'vi')}\n\n"
        f"Transcript:\n{transcript_text}"
    )
    logger.info("Pass 1/3: Extracting structured facts (%d chars)", len(transcript_text))
    return await _call_llm_json(
        llm,
        EXTRACT_SYSTEM_PROMPT,
        user_content,
        timeout=timeout,
        temperature=temperature,
        pass_label="Pass1-Extract",
        max_tokens=32768,
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
    existing_page_content: str | None,
    timeout: float,
    published_at: datetime | None = None,
    additional_instruction: str = "",
) -> dict[str, Any]:
    """Pass 2 (Analyze+Write combined): Compose final wiki page from extracted facts.

    The LLM analyzes cause-effect chains, investment implications, and speaker
    stance internally (reasoning ON), embedding them in ### subsections within
    each ## section.
    """
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

    # Full transcript for cross-reference (Pass 2 gets facts + transcript to verify).
    # DeepSeek v4 Flash 1M-token context easily fits the longest transcripts.
    transcript_ref = f"Transcript goc (de kiem tra cheo):\n{transcript_text}"

    t0_prefix = ""
    if published_at:
        t0_prefix = f"Video phat hanh ngay: {published_at.strftime('%Y-%m-%d')}\n\n"

    user_parts = [
        f"{t0_prefix}Phan loai:\n- Chu de: {classification.get('main_topic', '')}\n"
        f"{domain_info}{entities_info}"
        f"- Chu de phu: {', '.join(classification.get('subtopics', []))}\n"
        f"- Ngon ngu: {classification.get('language', 'vi')}\n"
        f"- Tom tat: {classification.get('summary_3sentences', '')}",
        f"DU KIEN TRICH XUAT:\n{facts_json}",
        transcript_ref,
    ]

    if existing_page_content:
        overview = _build_page_overview(existing_page_content)
        user_parts.insert(
            0,
            f"**CHE DO: CAP NHAT TRANG HIEN CO**\n"
            f"Trang wiki da co {overview.count('[')} section."
            f" Day la danh sach TAT CA section da co:\n\n"
            f"{overview}\n\n"
            f"**QUAN TRONG:** Chi viet CAC SECTION MOI, khong co trong danh sach tren.\n"
            f"KHONG viet lai, copy, hay tom tat cac section da co.\n"
            f"Moi section moi phai bo sung goc nhin hoac du lieu CHUA CO trong trang hien tai.\n"
            f"Neu transcript khong co gi moi, tra ve sections = [].",
        )

    if additional_instruction:
        user_parts.append(additional_instruction)
    user_content = "\n\n---\n\n".join(user_parts)
    logger.info("Pass 2/2: Analyze+Write combined (context: %d chars)", len(user_content))
    write_thinking = settings.wiki_write_thinking_enabled
    write_max_tokens = 131072 if write_thinking else 65536
    logger.info(
        "Pass 2/2: mode=analyze_write thinking=%s max_tokens=%d",
        write_thinking,
        write_max_tokens,
    )
    data = await _call_llm_json(
        llm,
        WRITE_SYSTEM_PROMPT,
        user_content,
        temperature=0.3,
        timeout=timeout,
        pass_label="Pass2-AnalyzeWrite",
        enable_thinking=write_thinking,
        max_tokens=write_max_tokens,
    )

    # Structural validation: check ### subsections in each section
    _validate_wiki_output(data)

    return data


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
            orm.PageSection.keywords,
        )
        .where(orm.PageSection.page_id == page.id)
        .order_by(orm.PageSection.section_order)
    )
    sections_data = [
        {
            "title": r[0],
            "content_markdown": r[1],
            "section_order": r[2],
            "source_ref": r[3],
            "keywords": r[4] or [],
        }
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
        sec_title = sec.get("title", "")
        # Strip leading ## Title header if LLM included it in content.
        sec_content = _strip_section_header(sec_content, sec_title)
        # Guard: reject thin/non-substantive section content (< 200 chars).
        if not sec_content or len(sec_content.strip()) < 200:
            logger.warning(
                "Skipping section '%s' in new page: content too short (%d chars, min 200)",
                sec_title,
                len(sec_content.strip()),
            )
            continue
        section = orm.PageSection(
            page_id=page.id,
            source_id=source_id,
            section_order=sec.get("order", idx),
            title=sec_title,
            content_markdown=sec_content,
            keywords=sec.get("keywords", []),
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

    for _idx, sec in enumerate(data.get("sections", [])):
        sec_content = sec.get("content_markdown", "")
        sec_title = sec.get("title", "")
        # Strip leading ## Title header if LLM included it in content.
        sec_content = _strip_section_header(sec_content, sec_title)
        # Guard: reject thin/non-substantive section content (< 200 chars).
        # The WRITE prompt requires "TỐI THIỂU 200 từ" per section. Content below
        # this threshold is either truncated, a thin duplicate of an existing
        # section, or an LLM placeholder — not real content.
        if not sec_content or len(sec_content.strip()) < 200:
            logger.warning(
                "Skipping section '%s': content too short (%d chars, min 200)",
                sec_title,
                len(sec_content.strip()),
            )
            continue
        section = orm.PageSection(
            page_id=page.id,
            source_id=source_id,
            section_order=max_order + 1,
            title=sec_title,
            content_markdown=sec_content,
            keywords=sec.get("keywords", []),
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
    segments: list[dict] | None = None,
    video_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run Pass 1: structured fact extraction from full transcript.

    When ``settings.wiki_chunking_enabled`` and timestamped ``segments`` are
    available, runs a map-reduce pipeline: each chunk is extracted separately
    (scoped to its time range, with retry + jittered 429 backoff) and the
    results are merged deterministically by ``fact_merger``. Otherwise falls
    back to the single-pass extraction.

    Returns:
        Tuple of (facts_dict, merged_classification).
    """
    detected_lang = _detect_transcript_language(transcript_text)
    if classification_hint is None:
        classification_hint = {
            "main_topic": "",
            "domain": "",
            "subtopics": [],
            "key_entities": [],
            "language": "vi",
            "summary_3sentences": "",
        }
    hint_lang = classification_hint.get("language") or "vi"
    if detected_lang != "mixed" and hint_lang != detected_lang:
        logger.info(
            "Transcript language detected as '%s' (classification hint: '%s') — overriding",
            detected_lang, hint_lang,
        )
        classification_hint["language"] = detected_lang

    lang = classification_hint.get("language")
    cleaned = _preprocess_transcript(transcript_text, lang=lang)

    use_chunked = (
        settings.wiki_chunking_enabled
        and segments is not None
        and len(segments) > 0
    )
    if use_chunked:
        from llm_wiki.application.use_cases.ingestion.transcript_chunker import (
            chunk_transcript,
        )

        chunks = chunk_transcript(segments)
        if chunks:
            facts = await _run_chunked_extraction(
                llm,
                cleaned,
                classification_hint,
                timeout,
                published_at,
                chunks,
                video_id,
            )
            logger.info(
                "Pass 1 (map-reduce) OK: %d chunks -> %d numbers, %d events, %d claims",
                len(chunks),
                len(facts.get("numbers", [])),
                len(facts.get("events", [])),
                len(facts.get("key_claims", [])),
            )
        else:
            logger.info(
                "Chunking skipped (no usable segments or duration < threshold) — single pass"
            )
            facts = await _pass_extract(
                llm,
                cleaned,
                classification_hint,
                timeout,
                published_at=published_at,
                video_id=video_id,
            )
    else:
        facts = await _pass_extract(
            llm,
            cleaned,
            classification_hint,
            timeout,
            published_at=published_at,
            video_id=video_id,
        )
    # P5: deterministic number normalization (post-merge, pre-persist).
    try:
        from llm_wiki.application.use_cases.ingestion.number_normalizer import normalize_facts

        facts = normalize_facts(facts)
    except Exception as exc:
        logger.warning("Number normalization skipped: %s", exc)

    logger.info(
        "Pass 1 OK: %d companies, %d people, %d numbers, %d events, %d relationships,"
        " %d entity_relations",
        len(facts.get("entities", {}).get("companies", [])),
        len(facts.get("entities", {}).get("people", [])),
        len(facts.get("numbers", [])),
        len(facts.get("events", [])),
        len(facts.get("relationships", [])),
        len(facts.get("entity_relations", []) or []),
    )

    merged_classification = dict(classification_hint)
    if facts.get("classification"):
        try:
            cls_data = facts["classification"]
            merged_classification = {
                "main_topic": cls_data.get("main_topic", classification_hint.get("main_topic", "")),
                "domain": cls_data.get("domain", classification_hint.get("domain", "")),
                "subtopics": cls_data.get("subtopics", classification_hint.get("subtopics", [])),
                "key_entities": cls_data.get(
                    "key_entities", classification_hint.get("key_entities", [])
                ),
                "language": cls_data.get("language", classification_hint.get("language", "vi")),
                "summary_3sentences": cls_data.get(
                    "summary_3sentences", classification_hint.get("summary_3sentences", "")
                ),
                "existing_pages_to_update": cls_data.get(
                    "existing_pages_to_update",
                    classification_hint.get("existing_pages_to_update", []),
                ),
            }
            logger.info("Using classification from Pass 1 (merged)")
        except Exception as exc:
            logger.warning("Failed to build classification from facts: %s - using hint", exc)

    # Deterministic transcript language wins over LLM drift: flash models often
    # mislabel Vietnamese transcripts as English, which would force the wiki
    # page to be written in English.
    if detected_lang != "mixed":
        if merged_classification.get("language") != detected_lang:
            logger.info(
                "Pass 1 classified language as '%s' — correcting to detected '%s'",
                merged_classification.get("language"), detected_lang,
            )
        merged_classification["language"] = detected_lang

    if not merged_classification.get("main_topic"):
        logger.warning("Pass 1 produced empty classification - caller should use cold fallback")

    return facts, merged_classification


_CLAIM_STOPWORDS = {
    "của", "với", "cho", "các", "theo", "khi", "này", "đó", "từ", "về",
    "trên", "dưới", "như", "còn", "đã", "sẽ", "đang", "được", "không",
    "cũng", "nhưng", "và", "là", "một", "những", "vào", "ra", "lên",
}


def _number_variants(value: str) -> list[str]:
    """Canonicalize a number string into variants for text search."""
    v = value.strip().lower()
    variants = {v, v.replace(" ", ""), v.replace(".", ","), v.replace(",", ".")}
    return [x for x in variants if x]


def _claim_represented(claim_text: str, page_text: str) -> bool:
    """Heuristic: is a key claim reflected in the page?

    Requires ≥ 40% of the claim's significant words (len ≥ 3, non-stopword)
    to appear in the page text. Paraphrase-tolerant; substring matching would
    over-trigger since the LLM rewrites sentences.
    """
    words = {
        w for w in re.findall(r"\w+", claim_text.lower())
        if len(w) >= 3 and w not in _CLAIM_STOPWORDS
    }
    if not words:
        return True
    page_lower = page_text.lower()
    hits = sum(1 for w in words if w in page_lower)
    return hits / len(words) >= 0.4


async def _verify_and_repair(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    facts: dict[str, Any],
    data: dict[str, Any],
    timeout: float,
    published_at: datetime | None = None,
    existing_page_content: str | None = None,
) -> dict[str, Any]:
    """Post-write quality gates.

    1. Numeric fidelity (warning-only): every Pass-1 number's value should
       appear in the page. Catches the most dangerous hallucination (wrong
       financial figures) without blocking on false positives (dates, years).
    2. Key-claim coverage: if > 30% of extracted speaker claims are not
       represented, issue ONE bounded repair call to Pass 2 listing nothing
       extra — the repair simply re-runs with the same facts (the missing
       claims are re-emphasized by the original prompt). Cost-capped.

    Never raises — warnings and best-effort repair only.
    """
    try:
        sections = data.get("sections", []) or []
        page_text = " ".join(
            [data.get("page_title", ""), data.get("summary", "")]
            + [str(s.get("content_markdown", "")) for s in sections]
        ).lower()

        numbers = facts.get("numbers") or []
        if numbers:
            missing = []
            for n in numbers:
                if not isinstance(n, dict):
                    continue
                val = str(n.get("value", "")).strip()
                if not val:
                    continue
                if not any(v in page_text for v in _number_variants(val)):
                    missing.append(val)
            if missing:
                logger.warning(
                    "Numeric fidelity: %d/%d Pass-1 numbers not found in page '%s': %s",
                    len(missing), len(numbers), data.get("page_title", "?"), missing[:10],
                )

        # Finance-array values (P3): same numeric fidelity check on the values
        # of market_snapshots / company_financials / macro_series /
        # supply_demand / valuations / other_financial_facts.
        finance_arrays = (
            "market_snapshots",
            "company_financials",
            "macro_series",
            "supply_demand",
            "valuations",
            "other_financial_facts",
        )
        finance_values: list[str] = []
        for arr in finance_arrays:
            for item in facts.get(arr, []) or []:
                if not isinstance(item, dict):
                    continue
                for field in ("raw_value", "value"):
                    val = str(item.get(field) or "").strip()
                    if val:
                        finance_values.append(val)
                        break
        if finance_values:
            missing_fin = [
                v for v in finance_values if not any(x in page_text for x in _number_variants(v))
            ]
            if missing_fin:
                logger.warning(
                    "Numeric fidelity (finance arrays): %d/%d values not found in page '%s': %s",
                    len(missing_fin),
                    len(finance_values),
                    data.get("page_title", "?"),
                    missing_fin[:10],
                )

        claims = facts.get("key_claims") or []
        if claims:
            missing_claims = [
                (c.get("claim") or "").strip()
                for c in claims
                if isinstance(c, dict) and (c.get("claim") or "").strip()
                and not _claim_represented(c["claim"], page_text)
            ]
            if missing_claims and len(missing_claims) / len(claims) > 0.3:
                logger.warning(
                    "Coverage: %d/%d key claims missing from page '%s' — issuing bounded repair",
                    len(missing_claims), len(claims), data.get("page_title", "?"),
                )
                try:
                    repaired = await _pass_write(
                        llm,
                        transcript_text,
                        classification,
                        facts,
                        existing_page_content,
                        timeout=min(timeout, 300.0),
                        published_at=published_at,
                    )
                    if repaired.get("sections"):
                        logger.info(
                            "Coverage repair produced page '%s' (%d sections)",
                            repaired.get("page_title", "?"), len(repaired.get("sections", [])),
                        )
                        return repaired
                except Exception as exc:
                    logger.warning("Coverage repair failed, keeping original page: %s", exc)
    except Exception as exc:
        logger.warning("Post-write verification skipped: %s", exc)
    return data


async def _run_synthesis_passes(
    llm: LLMClientPort,
    transcript_text: str,
    classification: dict[str, Any],
    facts: dict[str, Any],
    existing_page_content: str | None = None,
    timeout: float = 450.0,
    published_at: datetime | None = None,
    segments: list[dict] | None = None,
    video_id: str | None = None,
    raw_transcript: str | None = None,
) -> dict[str, Any]:
    """Run Pass 2 (Analyze+Write combined) with existing page context.

    When ``settings.wiki_reflect_enabled``, runs Pass 3 Reflect & Verify
    (thinking ON, compact JSON delta): number/date/unit corrections applied
    programmatically, high-priority missing facts filled by one bounded
    rewrite. Otherwise falls back to the heuristic ``_verify_and_repair``.

    ``raw_transcript`` must be the UNMODIFIED transcript (preprocessing is
    applied only for the write prompt) so reflect quotes stay verbatim.

    Returns:
        Dict with keys: "wiki", "facts", "classification"
    """
    detected_lang = _detect_transcript_language(transcript_text)
    if detected_lang != "mixed":
        classification["language"] = detected_lang

    lang = classification.get("language")
    cleaned = _preprocess_transcript(transcript_text, lang=lang)

    try:
        data = await _pass_write(
            llm,
            cleaned,
            classification,
            facts,
            existing_page_content,
            timeout=timeout * 0.6,
            published_at=published_at,
        )
        logger.info(
            "Pass 2 OK: wiki page '%s' (%d sections)",
            data.get("page_title", "?"),
            len(data.get("sections", [])),
        )
    except Exception as exc:
        logger.error("Pass 2 (Analyze+Write) failed: %s", exc)
        raise RuntimeError("Wiki page generation failed") from exc

    if settings.wiki_reflect_enabled:
        try:
            delta = await _pass_reflect(
                llm,
                _draft_to_markdown(data),
                facts,
                raw_transcript or transcript_text,
                segments,
                video_id,
                timeout=min(timeout * 0.4, 600.0),
            )
            errors = delta.get("errors") or []
            if errors:
                data, applied = _apply_corrections(data, errors)
                logger.info("Reflect: %d corrections applied", applied)
            high_missing = [
                m
                for m in (delta.get("missing_facts") or [])
                if isinstance(m, dict)
                and m.get("importance") == "high"
                and float(m.get("confidence") or 0.0) >= 0.7
            ]
            coverage = delta.get("coverage_by_section") or []
            low_coverage = any(
                float(c.get("coverage_ratio") or 1.0) < 0.5 for c in coverage if isinstance(c, dict)
            )
            if high_missing and low_coverage:
                topics = "\n".join(
                    f"- {m.get('topic')} (fact_id={m.get('fact_id')}, "
                    f"evidence: {m.get('evidence_quote')[:200]}, start_time={m.get('start_time')})"
                    for m in high_missing[:10]
                )
                rewrite = await _pass_write(
                    llm,
                    cleaned,
                    classification,
                    facts,
                    _draft_to_markdown(data),
                    timeout=min(timeout * 0.4, 300.0),
                    published_at=published_at,
                    additional_instruction=(
                        "**BO SUNG CAC SECTION MOI** (kem bang chung evidence_quote + "
                        f"start_time):\n{topics}\nChi viet cac section CHUA CO trong bai."
                    ),
                )
                data = _merge_rewrite_sections(data, rewrite)
        except Exception as exc:
            logger.warning("Reflect & Verify failed, falling back to heuristic: %s", exc)
            data = await _verify_and_repair(
                llm, cleaned, classification, facts, data,
                timeout=timeout * 0.6, published_at=published_at,
                existing_page_content=existing_page_content,
            )
    else:
        # Post-write quality gates: numeric fidelity (warning-only) and key-claim
        # coverage (bounded single repair call). See _verify_and_repair.
        data = await _verify_and_repair(
            llm, cleaned, classification, facts, data,
            timeout=timeout * 0.6, published_at=published_at,
            existing_page_content=existing_page_content,
        )

    return {"wiki": data, "facts": facts, "classification": classification}



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

        try:
            _segments = (item.transcript_json or {}).get("segments") or None
        except Exception:
            _segments = None
        facts, effective_classification = await _run_extraction_pass(
            llm=self._llm,
            transcript_text=transcript_text,
            classification_hint=classification,
            published_at=published_at_val,
            timeout=timeout,
            segments=_segments,
            video_id=item.external_id,
        )

        # Persist Pass-1 structured facts on the source item (source-scoped,
        # not page-scoped — pages merge multiple sources and would overwrite).
        # Enables provenance and post-write numeric/claim verification.
        if isinstance(facts, dict):
            try:
                item.key_claims = facts.get("key_claims") or []
                item.pass1_numbers = facts.get("numbers") or []
                item.pass1_facts = facts
            except Exception as exc:
                logger.warning("Failed to persist Pass-1 facts on item %s: %s", item.id, exc)

        # If Pass 1 failed to classify and caller provided a fallback, use it
        if (
            not effective_classification.get("main_topic")
            and classification
            and classification.get("main_topic")
        ):
            logger.warning(
                "Pass 1 classification empty - using provided classification as fallback"
            )
            effective_classification = classification

        if not effective_classification.get("main_topic"):
            raise RuntimeError("Cannot proceed: no classification available from Pass 1 or caller")

        # Step 2: Build summary_vector from merged classification if not provided
        if summary_vector is None or not summary_vector:
            classification_text = effective_classification.get(
                "summary_3sentences"
            ) or effective_classification.get("main_topic", "")
            if classification_text:
                emb = await self._embedder.embed(classification_text)
                summary_vector = emb.vector
            else:
                raise RuntimeError("Failed to build summary_vector from classification")

        # Step 3: Vector search for existing matching pages
        matches = await _vector_search_existing_pages(summary_vector, db)

        # Step 4: Pass 2 (Analyze+Write combined)
        # Use existing page context if a match passes multi-criteria gates
        # Extract entity names from classification for gate 3 (entity Jaccard)
        new_entity_names: list[str] = []
        if effective_classification.get("key_entities"):
            ke_list = effective_classification["key_entities"]
            if isinstance(ke_list, list):
                new_entity_names = [k["name"] if isinstance(k, dict) else str(k) for k in ke_list]

        best_match: orm.Page | None = None
        if matches:
            for candidate, sim in matches:
                should_merge, reason = _should_merge(
                    published_at_val,
                    candidate,
                    new_entity_names,
                    sim,
                )
                if should_merge:
                    best_match = candidate
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
            existing_page_content = (
                "\n\n".join(
                    f"## {s['title']}\n\n{s['content_markdown']}" for s in existing_sections
                )
                if existing_sections
                else best_match.content_markdown
            )

            llm_result = await _run_synthesis_passes(
                self._llm,
                transcript_text,
                effective_classification,
                facts,
                existing_page_content=existing_page_content,
                published_at=published_at_val,
                timeout=timeout,
                segments=_segments,
                video_id=item.external_id,
                raw_transcript=transcript_text,
            )
            llm_data = llm_result["wiki"]
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
                published_at=published_at_val,
                timeout=timeout,
                segments=_segments,
                video_id=item.external_id,
                raw_transcript=transcript_text,
            )
            llm_data = llm_result["wiki"]
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

        # Step 5: Event extraction + entity linking (non-fatal)
        try:
            await self._handle_event_extraction(
                facts=facts,
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

        return {
            "action": action,
            "page_id": page_id,
            "page_title": page.title,
        }

    async def _handle_event_extraction(
        self,
        facts: dict[str, Any],
        source_id_val: UUID,
        page_id_val: UUID,
        published_at_val: datetime | None,
        db: AsyncSession,
    ) -> None:
        """Extract, deduplicate, and store events; link entities and cause-effect chains.

        Uses the ported legacy event_extractor and event_linker logic.
        Cause-effect chains are extracted from facts.entity_relations (Pass 1 entity
        relations) instead of a separate analysis pass.
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

        # Link cause-effect chains from facts entity_relations (Pass 1)
        cause_effect_relationships = facts.get("entity_relations", []) or []
        await link_cause_effect_chains(
            llm=self._llm,
            embedder=self._embedder,
            cause_effect_chains=cause_effect_relationships,
            page_id=page_id_val,
            db=db,
        )

        # Detect contradictions against other sources
        await detect_contradictions(page_id=page_id_val, db=db)


async def _run_chunked_extraction(
    llm: LLMClientPort,
    cleaned_transcript: str,
    classification_hint: dict[str, Any],
    timeout: float,
    published_at: datetime | None,
    chunks: list,
    video_id: str | None,
) -> dict[str, Any]:
    """Map step of chunked extraction: extract each chunk with retry/backoff.

    A chunk is retried once; if it still fails it is skipped with a warning
    (partial coverage beats a dead job). If more than half the chunks fail,
    the whole pass raises so the consumer retries the item.
    """
    import random

    from llm_wiki.application.use_cases.ingestion.fact_merger import merge_chunk_facts

    # 300s per chunk: canary-verified LLM API latency is ~200-280s for a
    # ~10K-char finance chunk; 240s caused frequent timeouts. Still fits the
    # 3600s job budget (8 chunks x 300s + write + reflect).
    per_chunk_timeout = min(timeout, 300.0)
    chunks_facts: list[dict] = []
    failures = 0

    for idx, chunk in enumerate(chunks):
        try:
            chunk_facts = await _pass_extract(
                llm,
                chunk.text,
                classification_hint,
                per_chunk_timeout,
                published_at=published_at,
                chunk_range=(chunk.start_time, chunk.end_time),
                video_id=video_id,
            )
            chunks_facts.append(chunk_facts)
            logger.info(
                "Pass 1 chunk %d/%d (%d-%ds): %d numbers, %d events",
                idx + 1,
                len(chunks),
                int(chunk.start_time),
                int(chunk.end_time),
                len(chunk_facts.get("numbers", [])),
                len(chunk_facts.get("events", [])),
            )
        except Exception as exc:
            failures += 1
            logger.warning(
                "Pass 1 chunk %d/%d failed (%s) — retrying once",
                idx + 1,
                len(chunks),
                str(exc)[:200],
            )
            try:
                await asyncio.sleep(random.uniform(5.0, 15.0))  # jittered backoff
                chunk_facts = await _pass_extract(
                    llm,
                    chunk.text,
                    classification_hint,
                    per_chunk_timeout,
                    published_at=published_at,
                    chunk_range=(chunk.start_time, chunk.end_time),
                    video_id=video_id,
                )
                chunks_facts.append(chunk_facts)
                failures -= 1
            except Exception as retry_exc:
                logger.warning(
                    "Pass 1 chunk %d/%d permanently failed: %s — skipping chunk",
                    idx + 1,
                    len(chunks),
                    str(retry_exc)[:200],
                )

    if failures > 0 and failures / max(len(chunks), 1) > 0.5:
        raise RuntimeError(
            f"Pass 1 map-reduce: {failures}/{len(chunks)} chunks failed"
        )

    return merge_chunk_facts(chunks_facts, classification_hint)


# ---------------------------------------------------------------------------
# Pass 3: Reflect & Verify helpers
# ---------------------------------------------------------------------------


def _draft_to_markdown(data: dict[str, Any]) -> str:
    """Serialize a wiki draft dict into the markdown form _pass_write expects.

    Mirrors the storage shape (page_title + summary + sections with
    ``## title`` headers) so ``_build_page_overview`` and the bounded rewrite
    can consume it as ``existing_page_content``.
    """
    parts: list[str] = []
    if data.get("page_title"):
        parts.append(f"# {data['page_title']}")
    if data.get("summary"):
        parts.append(f"{data['summary']}\n")
    for sec in data.get("sections", []) or []:
        title = sec.get("title") or ""
        content = sec.get("content_markdown") or ""
        if not title and not content:
            continue
        parts.append(f"## {title}\n\n{content}")
    return "\n\n".join(parts)


def _apply_corrections(draft: dict[str, Any], errors: list[dict]) -> tuple[dict[str, Any], int]:
    """Apply number/date/unit corrections programmatically, per section.

    A correction is applied only when ``page_says`` matches EXACTLY ONCE within
    the named section (variant-aware). Ambiguous or absent matches are skipped
    with a warning — never guess.
    """
    applied = 0
    sections = draft.get("sections", []) or []
    by_title = {str(s.get("title", "")): s for s in sections}
    for err in errors:
        if not isinstance(err, dict):
            continue
        page_says = str(err.get("page_says") or "").strip()
        correct = str(err.get("correct_value") or "").strip()
        if not page_says or not correct:
            continue
        section = str(err.get("section") or "")
        target = by_title.get(section)
        if target is None:
            # Fall back to scanning every section.
            for sec in sections:
                if page_says in (sec.get("content_markdown") or ""):
                    target = sec
                    break
        if target is None:
            logger.warning("Reflect correction skipped (section not found): %s", page_says)
            continue
        content = target.get("content_markdown") or ""
        variants = _number_variants(page_says)
        matches = [v for v in variants if v in content]
        if len(matches) == 1 and content.count(matches[0]) == 1:
            target["content_markdown"] = content.replace(matches[0], correct)
            applied += 1
            logger.info(
                "Reflect correction applied in '%s': %s -> %s", section, matches[0], correct
            )
        else:
            logger.warning(
                "Reflect correction skipped (match=%d, count per variant) in '%s': %s",
                len(matches), section, page_says,
            )
    if applied:
        draft["content_markdown"] = _draft_to_markdown(draft)
    return draft, applied


def _merge_rewrite_sections(draft: dict[str, Any], rewrite: dict[str, Any]) -> dict[str, Any]:
    """Append sections produced by a bounded rewrite, deduplicating by title."""
    existing_titles = {str(s.get("title", "")).strip() for s in draft.get("sections", []) or []}
    next_order = len(draft.get("sections", []) or [])
    added = 0
    for sec in rewrite.get("sections", []) or []:
        if not isinstance(sec, dict):
            continue
        title = str(sec.get("title", "")).strip()
        if not title or title in existing_titles:
            continue
        new_sec = dict(sec)
        new_sec["order"] = next_order
        next_order += 1
        draft.setdefault("sections", []).append(new_sec)
        existing_titles.add(title)
        added += 1
    if added:
        logger.info("Reflect rewrite appended %d new section(s)", added)
        draft["content_markdown"] = _draft_to_markdown(draft)
    return draft


async def _pass_reflect(
    llm: LLMClientPort,
    draft_md: str,
    facts: dict[str, Any],
    raw_transcript: str,
    segments: list[dict] | None,
    video_id: str | None,
    timeout: float,
) -> dict[str, Any]:
    """Pass 3: audit the draft against facts + RAW transcript (thinking ON)."""
    facts_json = json.dumps(facts, ensure_ascii=False)[:30000]
    head = raw_transcript[:30000]
    tail = raw_transcript[-30000:] if len(raw_transcript) > 30000 else ""
    segment_index = ""
    if segments:
        lines = [
            f"{float(s.get('start', 0)):.0f}s: {(s.get('text') or '')[:160]}"
            for s in segments[:2000]
        ]
        segment_index = "\n".join(lines)

    user_content = "\n\n---\n\n".join(
        [
            f"VIDEO ID: {video_id or 'unknown'}\n\nBAN NHAP BAI WIKI:\n{draft_md}",
            f"KHO DU KIEN (facts):\n{facts_json}",
            f"TRANSCRIPT GOC (dau):\n{head}",
            f"TRANSCRIPT GOC (cuoi):\n{tail}" if tail else "",
            f"SEGMENT INDEX (start_time -> text):\n{segment_index}" if segment_index else "",
        ]
    )
    logger.info("Pass 3/3: Reflect & Verify (context: %d chars)", len(user_content))
    return await _call_llm_json(
        llm,
        REFLECT_SYSTEM_PROMPT,
        user_content,
        timeout=timeout,
        temperature=0.2,
        max_tokens=16384,
        enable_thinking=True,
        allow_retry=False,
        pass_label="Pass3-Reflect",
    )
