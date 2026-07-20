"""Event timeline chain linking: connect cause-effect chains to canonical events.

Port of legacy app/services/event_linker.py to clean architecture.
Uses SQLAlchemy async ORM and the embedding port for vector search.
"""

from __future__ import annotations

import json
import logging
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import EmbeddingServicePort, LLMClientPort
from llm_wiki.infrastructure.persistence.postgres import models as orm

logger = logging.getLogger(__name__)

AMBIGUOUS_MIN = 0.65
AMBIGUOUS_MAX = 0.85


async def _llm_verify_event_link(
    llm: LLMClientPort,
    trigger_text: str,
    effect_text: str,
    trigger_event_title: str,
    effect_event_title: str,
    similarity: float,
) -> bool:
    """Use LLM to verify if two events have a causal relationship.

    Called when vector similarity is in the ambiguous range (0.65-0.85).
    Returns True if LLM confirms the relationship, False otherwise.
    """
    prompt = (
        "Bạn là chuyên gia phân tích sự kiện tài chính. Xác định xem hai sự kiện sau "
        "có mối quan hệ nhân-quả không.\n\n"
        f"Sự kiện A (nguyên nhân từ phân tích): {trigger_text}\n"
        f"Sự kiện B (ứng viên trong CSDL): {effect_event_title}\n"
        f"Vector similarity: {similarity:.2f}\n\n"
        "Trả lời CHỈ một JSON object:\n"
        '{"related": true/false, "confidence": 0.0-1.0, "reasoning": "lý do ngắn gọn"}'
    )
    try:
        resp = await llm.chat_completion_raw(
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=200,
        )
        content = resp.get("choices", [{}])[0].get("message", {}).get("content", "{}")
        content = content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
        result = json.loads(content)
        related = result.get("related", False)
        confidence = result.get("confidence", 0.0)
        logger.debug(
            "LLM verify: '%s' ↔ '%s' → related=%s confidence=%.2f",
            trigger_text[:60],
            effect_event_title[:60],
            related,
            confidence,
        )
        return related and confidence >= 0.6
    except Exception as exc:
        logger.warning("LLM event verification failed, using vector result: %s", exc)
        return similarity >= 0.80


async def _get_event_title(event_id: UUID, db: AsyncSession) -> str:
    """Get the title of a canonical event."""
    result = await db.execute(
        select(orm.EventCanonical.title).where(orm.EventCanonical.id == event_id)
    )
    row = result.first()
    return row[0] if row else "Unknown"


async def _find_event_by_text(
    search_text: str,
    db: AsyncSession,
    embedder: EmbeddingServicePort,
) -> tuple[UUID | None, float]:
    """Find closest EventCanonical by embedding similarity.

    Returns (event_id, similarity_score). similarity is cosine similarity (0-1).
    """
    if not search_text.strip():
        return None, 0.0

    embedding_result = await embedder.embed(search_text)
    embedding = embedding_result.vector if embedding_result else []
    if not embedding or all(v == 0.0 for v in embedding):
        return None, 0.0

    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    sql = text(
        """
        SELECT id, 1.0 - (canonical_embedding <=> :vec) AS similarity
        FROM event_canonicals
        WHERE canonical_embedding IS NOT NULL
        ORDER BY canonical_embedding <=> :vec
        LIMIT 1
        """
    )
    result = await db.execute(sql, {"vec": vec_str})
    row = result.first()
    if row:
        return row[0], float(row[1])
    return None, 0.0


def _parse_confidence(value: str | float | None) -> float:
    if value is None:
        return 0.5
    if isinstance(value, (int, float)):
        return float(value)
    confidence_map = {"high": 0.8, "medium": 0.5, "low": 0.3}
    return confidence_map.get(str(value).lower(), 0.5)


async def link_cause_effect_chains(
    llm: LLMClientPort,
    embedder: EmbeddingServicePort,
    cause_effect_chains: list[dict[str, str | float]],
    page_id: UUID,
    db: AsyncSession,
) -> int:
    """Link Pass 2 cause_effect chains to canonical events.

    For each chain:
      1. Embed trigger/effect text → find closest EventCanonical via vector.
      2. If similarity in ambiguous range (0.65-0.85), verify with LLM.
      3. Create EventTimelineChain link.

    Returns count of links created.
    """
    if not cause_effect_chains:
        return 0

    linked = 0
    for chain in cause_effect_chains:
        try:
            trigger = str(chain.get("trigger", ""))
            effect_texts = chain.get("effects", [])
            if not trigger or not effect_texts:
                continue

            trigger_id, trigger_sim = await _find_event_by_text(trigger, db, embedder)
            if not trigger_id or trigger_sim < AMBIGUOUS_MIN:
                continue

            effect_text = str(effect_texts[0]) if effect_texts else ""
            effect_id, effect_sim = await _find_event_by_text(effect_text, db, embedder)
            if not effect_id or effect_id == trigger_id or effect_sim < AMBIGUOUS_MIN:
                continue

            relation_type = "causes"

            if trigger_sim < AMBIGUOUS_MAX or effect_sim < AMBIGUOUS_MAX:
                trigger_title = await _get_event_title(trigger_id, db)
                effect_title = await _get_event_title(effect_id, db)
                verified = await _llm_verify_event_link(
                    llm,
                    trigger,
                    effect_text,
                    trigger_title,
                    effect_title,
                    min(trigger_sim, effect_sim),
                )
                if not verified:
                    logger.debug(
                        "LLM rejected link: '%s' (%.2f) → '%s' (%.2f)",
                        trigger[:60],
                        trigger_sim,
                        effect_text[:60],
                        effect_sim,
                    )
                    continue

            existing = await db.execute(
                select(orm.EventTimelineChain).where(
                    orm.EventTimelineChain.from_event_id == trigger_id,
                    orm.EventTimelineChain.to_event_id == effect_id,
                    orm.EventTimelineChain.relation_type == relation_type,
                ).limit(1)
            )
            if existing.first():
                continue

            link = orm.EventTimelineChain(
                from_event_id=trigger_id,
                to_event_id=effect_id,
                relation_type=relation_type,
                description=trigger[:500],
                confidence=_parse_confidence(chain.get("confidence")),
            )
            db.add(link)
            linked += 1

        except Exception as exc:
            logger.warning("Failed to link chain: %s", exc)

    if linked:
        logger.info("Created %d event timeline chains (with LLM verification for ambiguous)", linked)
    return linked


async def detect_contradictions(
    page_id: UUID,
    db: AsyncSession,
) -> int:
    """Detect contradictory viewpoints on the same event.

    If EventObservation A has stance='bullish' or impact_direction='positive'
    and B has stance='bearish' or impact_direction='negative' for the same
    canonical event, create an EventTimelineChain(contradicts) between them.
    """
    result = await db.execute(
        text(
            """
            SELECT DISTINCT a.event_id
            FROM event_observations a
            JOIN event_observations b ON a.event_id = b.event_id
            WHERE a.page_id = :page_id
              AND b.page_id != a.page_id
              AND (
                (a.stance IN ('bullish') AND b.stance IN ('bearish'))
                OR (a.impact_direction = 'positive' AND b.impact_direction = 'negative')
                OR (a.impact_direction = 'negative' AND b.impact_direction = 'positive')
              )
            """
        ),
        {"page_id": page_id},
    )
    rows = result.all()

    linked = 0
    for row in rows:
        event_id = row[0]
        obs_result = await db.execute(
            select(orm.EventObservation).where(
                orm.EventObservation.event_id == event_id,
                orm.EventObservation.page_id == page_id,
            ).limit(1)
        )
        our_obs = obs_result.scalar()
        if not our_obs:
            continue

        opposite_result = await db.execute(
            select(orm.EventObservation).where(
                orm.EventObservation.event_id == event_id,
                orm.EventObservation.page_id != page_id,
                ((orm.EventObservation.stance == "bullish") | (orm.EventObservation.impact_direction == "positive"))
                if (our_obs.stance == "bearish" or our_obs.impact_direction == "negative")
                else ((orm.EventObservation.stance == "bearish") | (orm.EventObservation.impact_direction == "negative")),
            ).limit(1)
        )
        opposite = opposite_result.scalar()
        if not opposite:
            continue

        existing = await db.execute(
            select(orm.EventTimelineChain).where(
                orm.EventTimelineChain.from_event_id == event_id,
                orm.EventTimelineChain.to_event_id == event_id,
                orm.EventTimelineChain.relation_type == "contradicts",
            ).limit(1)
        )
        if existing.first():
            continue

        link = orm.EventTimelineChain(
            from_event_id=event_id,
            to_event_id=event_id,
            relation_type="contradicts",
            description=f"Stance conflict: {our_obs.stance or 'unknown'} vs {opposite.stance or 'unknown'}",
            confidence=0.5,
        )
        db.add(link)
        linked += 1

    if linked:
        await db.flush()
        logger.info("Created %d contradiction links", linked)
    return linked
