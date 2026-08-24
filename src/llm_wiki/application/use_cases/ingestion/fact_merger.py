"""Merge per-chunk Pass-1 extraction facts into one coherent fact store.

Deterministic reduce step of the chunked extraction pipeline: deduplicates
entities/numbers/events/claims/relations across chunks while PRESERVING every
field of the original records (attribution, confidence, impact_direction,
nested entities, provenance...) — merging must never reconstruct a record from
a dedup key alone.

Schema-aware merge per array:
  - entities:     keyed by (category, canonical name)
  - numbers:      keyed by (value, unit, normalized context)
  - events:       keyed by (normalized_date, category) + description overlap
  - relationships:      keyed by (source, target, relation_type)
  - entity_relations:   keyed by (from, to, predicate)   <- DIFFERENT schema
  - key_claims:   keyed by normalized claim text
"""

from __future__ import annotations

import re

_ENTITY_CATEGORIES = (
    "companies",
    "people",
    "indices",
    "policies",
    "locations",
    "commodities",
    "sectors",
    "bonds",
    "cryptocurrencies",
    "financial_metrics",
)

_NON_ENTITY_TOP_KEYS = (
    "classification",
    "numbers",
    "events",
    "relationships",
    "key_claims",
    "market_context",
    "chunk_summary",
    "entity_relations",
    "chunk_summaries",
)

_CAP = re.compile(r"\s+")
_STOP = {"của", "và", "là", "các", "theo", "khi", "này", "đó", "với", "cho", "trong", "một"}


def _canonical(name: str) -> str:
    return _CAP.sub(" ", (name or "").strip().lower())


def _claim_key(claim: dict) -> str:
    words = [
        w
        for w in re.findall(r"\w+", (claim.get("claim") or "").lower())
        if len(w) >= 3 and w not in _STOP
    ]
    return " ".join(words)


def _event_key(event: dict) -> tuple[str, str]:
    return (
        str(event.get("normalized_date") or event.get("date") or ""),
        str(event.get("category") or ""),
    )


def _char_overlap(a: str, b: str) -> float:
    set_a, set_b = set(a), set(b)
    if not set_a or not set_b:
        return 0.0
    return len(set_a & set_b) / min(len(set_a), len(set_b))


def _dedup_by_key(items: list[dict], key_fn) -> list[dict]:
    """Dedup by key, keeping the FIRST record and merging extra fields into it."""
    seen: dict[object, dict] = {}
    order: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        k = key_fn(item)
        if k in seen:
            merged = seen[k]
            for f, v in item.items():
                if f not in merged or merged[f] in (None, "", [], {}):
                    merged[f] = v
        else:
            seen[k] = item
            order.append(item)
    return order


def _dedup_entities(chunks_facts: list[dict]) -> dict[str, list[dict]]:
    merged: dict[str, list[dict]] = {}
    for cat in _ENTITY_CATEGORIES:
        items: list[dict] = []
        for facts in chunks_facts:
            items.extend(facts.get("entities", {}).get(cat, []) or [])
        merged[cat] = _dedup_by_key(
            items, lambda e, c=cat: (c, _canonical(e.get("name") or ""))
        )
    return merged


def _dedup_numbers(chunks_facts: list[dict]) -> list[dict]:
    items: list[dict] = []
    for facts in chunks_facts:
        items.extend(facts.get("numbers", []) or [])
    return _dedup_by_key(
        items,
        lambda n: (
            str(n.get("value") or "").strip().lower(),
            str(n.get("unit") or "").strip().lower(),
            _canonical(n.get("context") or ""),
        ),
    )


def _dedup_events(chunks_facts: list[dict]) -> list[dict]:
    """Event dedup by (date, category) + description overlap >= 0.6.

    Field-preserving: keep the longest description, union entities/attribution
    and keep max confidence + impact_direction.
    """
    keyed: dict[tuple[str, str], list[dict]] = {}
    for facts in chunks_facts:
        for ev in facts.get("events", []) or []:
            if not isinstance(ev, dict):
                continue
            keyed.setdefault(_event_key(ev), []).append(ev)

    out: list[dict] = []
    for group in keyed.values():
        if not group:
            continue
        if len(group) == 1:
            out.append(group[0])
            continue
        primary = group[0]
        for other in group[1:]:
            if _char_overlap(primary.get("description", ""), other.get("description", "")) < 0.6:
                out.append(other)
                continue
            # Merge into primary, keeping the richer description.
            merged_conf = max(primary.get("confidence") or 0.0, other.get("confidence") or 0.0)
            if len(other.get("description", "")) > len(primary.get("description", "")):
                primary = dict(other)
            for field in ("attribution", "entities"):
                if isinstance(primary.get(field), dict) and isinstance(other.get(field), dict):
                    primary[field] = {**primary[field], **other[field]}
            if isinstance(primary.get("entities"), list) and isinstance(
                other.get("entities"), list
            ):
                primary["entities"] = _dedup_by_key(
                    primary["entities"] + other["entities"],
                    lambda e: _canonical(
                        (e.get("name") if isinstance(e, dict) else None) or str(e)
                    ),
                )
            primary["confidence"] = merged_conf
            if not primary.get("impact_direction"):
                primary["impact_direction"] = other.get("impact_direction")
        out.append(primary)
    return out


def _dedup_relationships(chunks_facts: list[dict]) -> list[dict]:
    """relationships[] uses source/target/relation_type (NOT predicate)."""
    items: list[dict] = []
    for facts in chunks_facts:
        items.extend(facts.get("relationships", []) or [])
    return _dedup_by_key(
        items,
        lambda r: (
            _canonical(r.get("source") or ""),
            _canonical(r.get("target") or ""),
            str(r.get("relation_type") or ""),
        ),
    )


def _dedup_entity_relations(chunks_facts: list[dict]) -> list[dict]:
    """entity_relations[] uses from/to/predicate (separate schema)."""
    items: list[dict] = []
    for facts in chunks_facts:
        items.extend(facts.get("entity_relations", []) or [])
    return _dedup_by_key(
        items,
        lambda r: (
            _canonical(r.get("from") or ""),
            _canonical(r.get("to") or ""),
            str(r.get("predicate") or ""),
        ),
    )


def _dedup_claims(chunks_facts: list[dict]) -> list[dict]:
    items: list[dict] = []
    for facts in chunks_facts:
        items.extend(facts.get("key_claims", []) or [])
    return _dedup_by_key(items, _claim_key)


def _merge_classifications(
    chunks_facts: list[dict], classification_hint: dict | None = None
) -> dict:
    """Caller-provided hint is PRIMARY; chunk data only fills missing fields.

    The "richest" chunk = the one with the most extracted facts (counts are a
    better signal than verbosity of chunk_summary).
    """
    base = dict(classification_hint or {})

    def _fact_count(facts: dict) -> int:
        return sum(
            len(facts.get(k, []) or [])
            for k in (
                "numbers",
                "events",
                "relationships",
                "key_claims",
                "entity_relations",
            )
        )

    best = max(chunks_facts, key=_fact_count, default={})
    cls = best.get("classification") or {}

    if not base.get("main_topic") and cls.get("main_topic"):
        base["main_topic"] = cls["main_topic"]
    if not base.get("domain") and cls.get("domain"):
        base["domain"] = cls["domain"]
    if not base.get("summary_3sentences") and cls.get("summary_3sentences"):
        base["summary_3sentences"] = cls["summary_3sentences"]
    if not base.get("language") and cls.get("language"):
        base["language"] = cls["language"]

    subtopics: list[str] = list(base.get("subtopics") or [])
    seen_sub = {_canonical(s) for s in subtopics}
    for facts in chunks_facts:
        for s in (facts.get("classification") or {}).get("subtopics", []) or []:
            if _canonical(s) not in seen_sub:
                subtopics.append(s)
                seen_sub.add(_canonical(s))
    if subtopics:
        base["subtopics"] = subtopics

    key_entities: list[str] = list(base.get("key_entities") or [])
    seen_ke = {_canonical(k) for k in key_entities}
    for facts in chunks_facts:
        for ke in (facts.get("classification") or {}).get("key_entities", []) or []:
            name = ke if isinstance(ke, str) else (ke.get("name") if isinstance(ke, dict) else "")
            if name and _canonical(name) not in seen_ke:
                key_entities.append(name)
                seen_ke.add(_canonical(name))
    if key_entities:
        base["key_entities"] = key_entities

    return base


def _merge_scalar(facts: dict, key: str, chunks_facts: list[dict], joiner: str = "\n") -> str:
    parts = [f.get(key) for f in chunks_facts if f.get(key)]
    if not parts:
        return facts.get(key) or ""
    return joiner.join(str(p).strip() for p in parts if str(p).strip())


def merge_chunk_facts(
    chunks_facts: list[dict], classification_hint: dict | None = None
) -> dict:
    """Merge a list of per-chunk extraction dicts into one coherent fact store.

    Preserves every top-level key present in the chunks; arrays are deduplicated
    schema-aware. Classification uses the caller hint as primary.
    """
    merged: dict = {"entities": {}}
    for facts in chunks_facts:
        if not isinstance(facts, dict):
            continue
        for key, value in facts.items():
            if key in _NON_ENTITY_TOP_KEYS or key == "entities":
                continue
            if key not in merged or merged[key] in (None, "", [], {}):
                merged[key] = value

    merged["entities"] = _dedup_entities(chunks_facts)
    merged["numbers"] = _dedup_numbers(chunks_facts)
    merged["events"] = _dedup_events(chunks_facts)
    merged["relationships"] = _dedup_relationships(chunks_facts)
    merged["entity_relations"] = _dedup_entity_relations(chunks_facts)
    merged["key_claims"] = _dedup_claims(chunks_facts)
    merged["classification"] = _merge_classifications(chunks_facts, classification_hint)

    summaries = [f.get("chunk_summary") for f in chunks_facts if f.get("chunk_summary")]
    if summaries:
        merged["chunk_summaries"] = summaries
    merged["market_context"] = _merge_scalar(merged, "market_context", chunks_facts)

    return merged
