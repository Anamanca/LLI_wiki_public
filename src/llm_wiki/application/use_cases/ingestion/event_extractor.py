"""Event extraction: capture Pass 1 events, deduplicate, store in DB.

Port of legacy app/services/event_extractor.py to clean architecture.
Uses raw SQLAlchemy ORM operations (AsyncSession) and the embedding port.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select, text, func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import EmbeddingServicePort
from llm_wiki.application.use_cases.ingestion.entity_relation_validator import validate_relation
from llm_wiki.infrastructure.persistence.postgres import models as orm

logger = logging.getLogger(__name__)

VECTOR_SIM_THRESHOLD = 0.85


def _parse_date(date_str: str | None) -> date | None:
    if not date_str:
        return None
    try:
        return date.fromisoformat(str(date_str)[:10])
    except (ValueError, TypeError):
        return None


def _build_exact_key(event: dict[str, Any]) -> tuple[str | None, str | None, str | None]:
    normalized_date = event.get("normalized_date") or event.get("date")
    category = event.get("category")
    entities = event.get("entities", {}) if isinstance(event.get("entities"), dict) else {}
    companies = entities.get("companies", []) if isinstance(entities, dict) else []
    top_entity = companies[0].get("name") if companies and isinstance(companies[0], dict) else None
    return (normalized_date, category, top_entity)


async def _dedup_exact_key(
    event: dict[str, Any],
    db: AsyncSession,
) -> UUID | None:
    key = _build_exact_key(event)
    if not any(key):
        return None
    stmt = (
        select(orm.EventCanonical.id)
        .where(
            orm.EventCanonical.normalized_date == _parse_date(key[0]),
            orm.EventCanonical.category == key[1],
        )
        .limit(5)
    )
    result = await db.execute(stmt)
    rows = result.all()
    for row in rows:
        if row[0] is not None:
            canonical = await db.get(orm.EventCanonical, row[0])
            if canonical and canonical.entities and key[2]:
                entities = canonical.entities if isinstance(canonical.entities, dict) else {}
                companies = entities.get("companies", []) if isinstance(entities, dict) else []
                top_entity = companies[0].get("name") if companies and isinstance(companies[0], dict) else None
                if top_entity and top_entity.lower() == key[2].lower():
                    return row[0]
            else:
                return row[0]
    return None


async def _dedup_vector(
    embedding: list[float],
    db: AsyncSession,
) -> UUID | None:
    if not embedding or all(v == 0.0 for v in embedding):
        return None
    vec_str = "[" + ",".join(str(v) for v in embedding) + "]"
    sql = text(
        """
        SELECT id, 1 - (canonical_embedding <=> :vec) AS similarity
        FROM event_canonicals
        WHERE canonical_embedding IS NOT NULL
          AND 1 - (canonical_embedding <=> :vec) >= :threshold
        ORDER BY canonical_embedding <=> :vec
        LIMIT 1
        """
    )
    result = await db.execute(sql, {"vec": vec_str, "threshold": VECTOR_SIM_THRESHOLD})
    row = result.first()
    if row:
        return row[0]
    return None


async def _create_canonical(
    event: dict[str, Any],
    embedding: list[float],
    db: AsyncSession,
) -> orm.EventCanonical:
    description = event.get("description", "")
    title = description[:200] if description else "Unknown Event"
    normalized_date = _parse_date(event.get("normalized_date") or event.get("date"))
    category = event.get("category")
    entities_data = {}
    if isinstance(event.get("entities"), dict):
        entities_data = event.get("entities", {})
    if not isinstance(entities_data, dict):
        entities_data = {}
    entities_data["impact_direction"] = event.get("impact_direction")

    canonical = orm.EventCanonical(
        title=title,
        normalized_date=normalized_date,
        category=category,
        entities=entities_data,
        importance_score=0.5,
        canonical_embedding=embedding,
    )
    db.add(canonical)
    await db.flush()
    return canonical


async def _create_observation(
    event_id: UUID,
    event: dict[str, Any],
    source_id: UUID,
    page_id: UUID,
    source_published_at: datetime | None,
    embedding: list[float],
    db: AsyncSession,
) -> orm.EventObservation:
    attribution = event.get("attribution", {}) if isinstance(event.get("attribution"), dict) else {}
    observation = orm.EventObservation(
        event_id=event_id,
        source_id=source_id,
        page_id=page_id,
        source_published_at=source_published_at,
        observation_type="opinion" if attribution.get("is_opinion") else "fact",
        description=event.get("description", ""),
        impact_direction=event.get("impact_direction"),
        metrics={
            "confidence": event.get("confidence", 0.5),
            "certainty": attribution.get("certainty"),
        },
        confidence=event.get("confidence", 0.5),
        embedding=embedding,
    )
    db.add(observation)
    await db.flush()
    return observation


async def _inc_observation_count(
    event_id: UUID,
    db: AsyncSession,
) -> None:
    """Increment observation_count atomically with deadlock retry."""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            await db.execute(
                update(orm.EventCanonical)
                .where(orm.EventCanonical.id == event_id)
                .values(
                    observation_count=orm.EventCanonical.observation_count + 1,
                    updated_at=func.now(),
                )
            )
            return
        except Exception as exc:
            err_str = str(exc).lower()
            is_deadlock = "deadlock" in err_str or "deadlock detected" in err_str
            if not is_deadlock or attempt >= max_retries - 1:
                raise
            backoff = 0.1 * (2 ** attempt)
            logger.warning(
                "Deadlock retry %d/%d for event %s, waiting %.2fs",
                attempt + 1,
                max_retries,
                event_id,
                backoff,
            )
            await asyncio.sleep(backoff)
            if attempt >= max_retries - 1:
                raise


async def _link_entities_to_event(
    event_data: dict[str, Any],
    event_id: UUID,
    db: AsyncSession,
) -> tuple[list[UUID], dict[str, UUID]]:
    """Extract entities from event_data, upsert into entities table, create links.

    Returns (linked_entity_ids, entity_map). entity_map maps canonical_name → entity_id.
    """
    entities_raw = event_data if isinstance(event_data, dict) else {}
    if not entities_raw:
        return [], {}

    entity_type_map = {
        "companies": "stock_ticker",
        "people": "person",
        "policies": "policy",
        "locations": "location",
        "commodities": "commodity",
        "sectors": "sector",
        "bonds": "bond",
        "cryptocurrencies": "cryptocurrency",
        "financial_metrics": "financial_metric",
    }
    linked_ids: list[UUID] = []
    entity_map: dict[str, UUID] = {}

    for category, default_type in entity_type_map.items():
        entries = entities_raw.get(category, [])
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            name = (entry.get("name") or "").strip()
            if not name:
                continue
            ticker = (entry.get("ticker") or "").upper() or None
            canonical = name.lower()
            etype = entry.get("type") or default_type

            stmt = (
                pg_insert(orm.Entity)
                .values(name=name, type=etype, canonical_name=canonical, ticker=ticker)
                .on_conflict_do_nothing(constraint="uq_entities_type_canonical")
            )
            await db.execute(stmt)
            await db.flush()

            result = await db.execute(
                select(orm.Entity.id).where(
                    orm.Entity.type == etype,
                    orm.Entity.canonical_name == canonical,
                ).limit(1)
            )
            entity_row = result.first()
            if not entity_row:
                continue

            entity_id = entity_row[0]
            linked_ids.append(entity_id)
            entity_map[canonical] = entity_id

            link_stmt = (
                pg_insert(orm.EventEntityLink)
                .values(
                    event_id=event_id,
                    entity_id=entity_id,
                    relationship_type="mentions",
                    confidence=event_data.get("confidence", 0.5),
                )
                .on_conflict_do_nothing(constraint="uq_event_entity_link")
            )
            await db.execute(link_stmt)

    indices = entities_raw.get("indices", [])
    if isinstance(indices, list):
        for idx in indices:
            if not isinstance(idx, dict):
                continue
            name = (idx.get("name") or "").strip()
            if not name:
                continue
            canonical = name.lower()
            etype = idx.get("type") or "market_index"

            stmt = (
                pg_insert(orm.Entity)
                .values(name=name, type=etype, canonical_name=canonical, ticker=None)
                .on_conflict_do_nothing(constraint="uq_entities_type_canonical")
            )
            await db.execute(stmt)
            await db.flush()

            result = await db.execute(
                select(orm.Entity.id).where(
                    orm.Entity.type == etype,
                    orm.Entity.canonical_name == canonical,
                ).limit(1)
            )
            entity_row = result.first()
            if not entity_row:
                continue

            entity_id = entity_row[0]
            linked_ids.append(entity_id)
            entity_map[canonical] = entity_id

            link_stmt = (
                pg_insert(orm.EventEntityLink)
                .values(
                    event_id=event_id,
                    entity_id=entity_id,
                    relationship_type="mentions",
                    confidence=event_data.get("confidence", 0.5),
                )
                .on_conflict_do_nothing(constraint="uq_event_entity_link")
            )
            await db.execute(link_stmt)

    return linked_ids, entity_map


async def _store_entity_relations(
    entity_relations_data: list[dict[str, Any]] | None,
    event_id: UUID,
    db: AsyncSession,
    entity_map: dict[str, UUID],
) -> int:
    """Store entity-to-entity relations from Pass 1 LLM output.

    Validates each relation via entity_relation_validator before INSERT.
    Returns count of relations actually stored.
    """
    if not entity_relations_data:
        return 0

    entity_ids = list(entity_map.values())
    type_result = await db.execute(
        select(orm.Entity.id, orm.Entity.type).where(orm.Entity.id.in_(entity_ids))
    )
    entity_types: dict[UUID, str] = {row[0]: row[1] for row in type_result.all()}

    stored = 0
    rejected = 0
    for rel in entity_relations_data:
        if not isinstance(rel, dict):
            continue
        from_name = (rel.get("from") or "").strip()
        to_name = (rel.get("to") or "").strip()
        if not from_name or not to_name:
            continue
        from_id = entity_map.get(from_name.lower())
        to_id = entity_map.get(to_name.lower())
        if not from_id or not to_id:
            continue
        predicate = (rel.get("predicate") or "").strip()
        if not predicate:
            continue

        from_type = entity_types.get(from_id, rel.get("from_type", "other"))
        to_type = entity_types.get(to_id, rel.get("to_type", "other"))
        validation = validate_relation(from_name, from_type, to_name, to_type, predicate)
        if not validation.valid:
            logger.debug("Rejected: %s —%s→ %s (%s)", from_name, predicate, to_name, validation.reason)
            rejected += 1
            continue

        result = await db.execute(
            pg_insert(orm.EntityRelation)
            .values(
                from_entity_id=from_id,
                to_entity_id=to_id,
                predicate=predicate,
                confidence=rel.get("confidence", 0.5),
                source_event_id=event_id,
            )
            .on_conflict_do_nothing(constraint="uq_entity_relations_from_to_predicate")
            .returning(orm.EntityRelation.id)
        )
        if result.scalar_one_or_none():
            stored += 1

    if rejected:
        logger.info("Stored %d relations, rejected %d", stored, rejected)
    return stored


async def extract_and_store_events(
    events: list[dict[str, Any]],
    source_id: UUID,
    page_id: UUID,
    source_published_at: datetime | None,
    db: AsyncSession,
    embedder: EmbeddingServicePort,
    entities_data: dict[str, Any] | None = None,
    entity_relations_data: list[dict[str, Any]] | None = None,
) -> int:
    """Main entry point: extract events from Pass 1 facts, dedup, store.

    2-pass dedup: exact key match → vector similarity ≥ 0.85 → create new.
    Links entities from top-level Pass 1 entities extraction.

    Returns count of events stored.
    """
    if not events:
        return 0

    stored = 0
    relations_stored = 0
    entity_map: dict[str, UUID] = {}
    first_event_id: UUID | None = None

    for event in events:
        try:
            description = event.get("description", "")
            if not description:
                continue

            embedding_result = await embedder.embed(description)
            embedding = embedding_result.vector if embedding_result else []

            existing_id = await _dedup_exact_key(event, db)
            if existing_id:
                if first_event_id is None:
                    first_event_id = existing_id
                await _create_observation(
                    existing_id, event, source_id, page_id, source_published_at, embedding, db
                )
                await _inc_observation_count(existing_id, db)
                stored += 1
                continue

            existing_id = await _dedup_vector(embedding, db)
            if existing_id:
                if first_event_id is None:
                    first_event_id = existing_id
                await _create_observation(
                    existing_id, event, source_id, page_id, source_published_at, embedding, db
                )
                await _inc_observation_count(existing_id, db)
                stored += 1
                continue

            canonical = await _create_canonical(event, embedding, db)
            if first_event_id is None:
                first_event_id = canonical.id
            await _create_observation(
                canonical.id, event, source_id, page_id, source_published_at, embedding, db
            )
            stored += 1

        except Exception as exc:
            logger.warning(
                "Failed to process event '%s': %s",
                event.get("description", "")[:80],
                exc,
            )

    if entities_data and first_event_id:
        _, entity_map = await _link_entities_to_event(entities_data, first_event_id, db)

    if entity_relations_data and entity_map and first_event_id:
        relations_stored = await _store_entity_relations(
            entity_relations_data, first_event_id, db, entity_map
        )

    logger.info(
        "Stored %d events, %d entity_relations for page_id=%s source_id=%s",
        stored,
        relations_stored,
        page_id,
        source_id,
    )
    return stored
