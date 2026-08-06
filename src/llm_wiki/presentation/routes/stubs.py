"""
Optional stub/read-only routes for the admin UI. Admin operational routes
(cron, scan status, health, restarts, api-keys) are mounted unconditionally
via src/llm_wiki/presentation/routes/admin.py.
"""

import contextlib
from collections import defaultdict
from uuid import UUID

import psutil
from fastapi import APIRouter, Depends, HTTPException
from datetime import timedelta

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.presentation.dependencies import get_db
from llm_wiki.shared.datetime_utils import now

router = APIRouter()

MASK = "***"


async def _count_done_today(db: AsyncSession) -> int:
    today_start = now().replace(hour=0, minute=0, second=0, microsecond=0)
    q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status.in_(["completed", "published"]),
        orm.SourceItem.started_at >= today_start,
    )
    return (await db.execute(q)).scalar() or 0


# ──────────────────────────────────────────
# Progress
# ──────────────────────────────────────────


@router.get("/progress")
async def progress(db: AsyncSession = Depends(get_db)):
    # global counts by status
    counts_query = select(
        orm.SourceItem.status,
        func.count(orm.SourceItem.id),
    ).group_by(orm.SourceItem.status)
    result = await db.execute(counts_query)
    status_counts = {r[0]: r[1] for r in result.all()}

    global_stats = {
        "pending": status_counts.get("pending", 0),
        "pending_transcribe": status_counts.get("pending_transcribe", 0),
        "waiting_for_wiki": status_counts.get("waiting_for_wiki", 0),
        "processing": status_counts.get("processing", 0),
        "done_today": await _count_done_today(db),
        "failed": status_counts.get("failed", 0),
        "rate_limited": status_counts.get("rate_limited", 0),
        "requires_membership": status_counts.get("requires_membership", 0),
    }

    # per_source breakdown
    per_source_query = (
        select(
            orm.Source.name,
            func.count(orm.SourceItem.id).label("total"),
            func.sum(
                case((orm.SourceItem.status.in_(["completed", "published"]), 1), else_=0)
            ).label("done"),
        )
        .join(orm.SourceItem, orm.Source.id == orm.SourceItem.source_id, isouter=True)
        .where(orm.Source.status == "active")
        .group_by(orm.Source.name)
    )
    result = await db.execute(per_source_query)
    per_source = []
    for r in result.all():
        total = r.total or 0
        done = r.done or 0
        per_source.append(
            {
                "name": r.name,
                "done": done,
                "total": total,
                "percent": round(done / total * 100, 1) if total > 0 else 0,
            }
        )

    # latest ingestion alerts — error/warning types for items that are
    # still active. Once an item reaches a terminal state (completed, failed,
    # unavailable, etc.) its stale error/retry logs are no longer relevant.
    alert_types = ["error", "rate_limit", "retry", "api_limit"]
    terminal_done = [
        "completed",
        "published",
        "skipped",
        "unavailable",
        "failed",
        "requires_membership",
        "no_captions",
        "no_captions_t3_fail",
        "scheduled",
        "rate_limited",
    ]
    alerts_q = (
        select(orm.IngestionLog)
        .where(orm.IngestionLog.event_type.in_(alert_types))
        .where(
            # Keep alert if there's no associated item (system-level alert)
            orm.IngestionLog.source_item_id.is_(None)
            # Keep alert only if item is still active (not terminal)
            | orm.IngestionLog.source_item_id.not_in(
                select(orm.SourceItem.id).where(orm.SourceItem.status.in_(terminal_done))
            )
        )
        .order_by(orm.IngestionLog.created_at.desc())
        .limit(20)
    )
    result = await db.execute(alerts_q)
    alerts = [
        {
            "id": str(a.id),
            "event_type": a.event_type,
            "message": a.message,
            "source_item_id": str(a.source_item_id) if a.source_item_id else None,
            "created_at": str(a.created_at) if a.created_at else None,
        }
        for a in result.scalars()
    ]

    # processing items
    processing_q = (
        select(orm.SourceItem, orm.Source.name)
        .join(orm.Source)
        .where(orm.SourceItem.status == "processing")
        .order_by(orm.SourceItem.started_at.desc())
        .limit(10)
    )
    result = await db.execute(processing_q)
    processing_items = []
    for item, src_name in result.all():
        elapsed = 0
        if item.started_at:
            elapsed = (now() - item.started_at).total_seconds()
        stage_label_map = {
            "transcribe": "Transcribe",
            "wiki": "Generate Wiki",
            "event_extract": "Event Extraction",
        }
        processing_items.append(
            {
                "id": str(item.id),
                "video_id": item.external_id,
                "title": item.title or "Unknown",
                "stage": item.status,
                "stage_label": stage_label_map.get(item.status, item.status or "Unknown"),
                "started_at": str(item.started_at) if item.started_at else None,
                "elapsed_seconds": int(elapsed),
                "source_name": src_name,
            }
        )

    # requires_membership count
    req_mem_q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status == "requires_membership"
    )
    req_count = (await db.execute(req_mem_q)).scalar() or 0

    return {
        "global": global_stats,
        "per_source": per_source,
        "alerts": alerts,
        "processing_items": processing_items,
        "requires_membership_count": req_count,
    }


# ──────────────────────────────────────────
# System Stats
# ──────────────────────────────────────────


@router.get("/system-stats")
async def system_stats():
    cpu = psutil.cpu_percent(interval=0.5)
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage("/")
    return {
        "cpu_percent": round(cpu, 1),
        "ram_used_gb": round(mem.used / (1024**3), 1),
        "ram_total_gb": round(mem.total / (1024**3), 1),
        "disk_used_gb": round(disk.used / (1024**3), 1),
        "disk_total_gb": round(disk.total / (1024**3), 1),
    }


# ──────────────────────────────────────────
# Source Detail
# ──────────────────────────────────────────


@router.get("/sources/{source_id}")
async def get_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err
    result = await db.execute(select(orm.Source).where(orm.Source.id == sid))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    # counts
    counts = {}
    for status_val in [
        "pending",
        "processing",
        "completed",
        "failed",
        "no_captions",
        "skipped",
        "rate_limited",
    ]:
        c = await db.execute(
            select(func.count(orm.SourceItem.id)).where(
                orm.SourceItem.source_id == sid,
                orm.SourceItem.status == status_val,
            )
        )
        counts[status_val] = c.scalar() or 0

    page_count = (
        await db.execute(select(func.count(orm.Page.id)).where(orm.Page.source_id == sid))
    ).scalar() or 0
    video_count = (
        await db.execute(
            select(func.count(orm.SourceItem.id)).where(orm.SourceItem.source_id == sid)
        )
    ).scalar() or 0

    return {
        "id": str(src.id),
        "name": src.name,
        "platform": src.platform,
        "external_id": src.external_id,
        "url": src.url,
        "added_at": str(src.added_at) if src.added_at else None,
        "last_checked_at": str(src.last_checked_at) if src.last_checked_at else None,
        "status": src.status,
        "config": src.config or {},
        "video_count": video_count,
        "page_count": page_count,
        "status_breakdown": counts,
    }


@router.patch("/sources/{source_id}")
async def update_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err
    result = await db.execute(select(orm.Source).where(orm.Source.id == sid))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    # partial update - for now just return current state
    await db.commit()
    return {
        "id": str(src.id),
        "name": src.name,
        "platform": src.platform,
        "external_id": src.external_id,
        "url": src.url,
        "added_at": str(src.added_at) if src.added_at else None,
        "last_checked_at": str(src.last_checked_at) if src.last_checked_at else None,
        "status": src.status,
        "config": src.config or {},
    }


@router.delete("/sources/{source_id}")
async def delete_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err
    result = await db.execute(select(orm.Source).where(orm.Source.id == sid))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    src.status = "inactive"
    await db.commit()
    return {"status": "deleted", "id": source_id}


@router.post("/sources/{source_id}/scan")
async def scan_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err
    result = await db.execute(select(orm.Source).where(orm.Source.id == sid))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")
    return {
        "status": "ok",
        "message": "Scan triggered (not yet implemented)",
        "new_items_found": 0,
        "restarted_rate_limited": 0,
        "restarted_failed": 0,
    }


# ──────────────────────────────────────────
# Source Items
# ──────────────────────────────────────────


@router.get("/sources/{source_id}/items")
async def list_source_items(
    source_id: str,
    status: str = None,
    db: AsyncSession = Depends(get_db),
):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err

    q = select(orm.SourceItem).where(orm.SourceItem.source_id == sid)
    if status:
        statuses = [s.strip() for s in status.split(",")]
        q = q.where(orm.SourceItem.status.in_(statuses))
    q = q.order_by(orm.SourceItem.created_at.desc()).limit(100)
    result = await db.execute(q)
    items = [
        {
            "id": str(item.id),
            "source_id": str(item.source_id),
            "external_id": item.external_id,
            "title": item.title,
            "url": item.url,
            "published_at": str(item.published_at) if item.published_at else None,
            "status": item.status,
            "retry_count": item.retry_count or 0,
            "priority": item.priority or 0,
            "error_message": item.error_message,
            "created_at": str(item.created_at) if item.created_at else None,
        }
        for item in result.scalars()
    ]
    return {"items": items, "total": len(items)}


@router.post("/sources/items/{item_id}/skip")
async def skip_source_item(item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        iid = UUID(item_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid item ID") from err
    result = await db.execute(select(orm.SourceItem).where(orm.SourceItem.id == iid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.status = "skipped"
    item.error_message = None
    await db.commit()
    return {"status": "skipped", "item_id": item_id, "message": "Marked as skipped"}


@router.post("/sources/items/{item_id}/retry")
async def retry_source_item(item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        iid = UUID(item_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid item ID") from err
    result = await db.execute(select(orm.SourceItem).where(orm.SourceItem.id == iid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.status = "pending"
    item.error_message = None
    item.retry_count = (item.retry_count or 0) + 1
    item.retry_after = None
    await db.commit()
    return {"status": "retrying", "item_id": item_id, "message": "Reset to pending"}


@router.post("/sources/items/{item_id}/transcript")
async def submit_transcript(item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        iid = UUID(item_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid item ID") from err
    result = await db.execute(select(orm.SourceItem).where(orm.SourceItem.id == iid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    # manual transcript submit requires body parsing; for now just acknowledge
    return {"status": "ok", "item_id": item_id, "wiki_action": "none"}


# ──────────────────────────────────────────
# Page Update (PATCH)
# ──────────────────────────────────────────


@router.patch("/pages/{page_id}")
async def update_page(page_id: str, db: AsyncSession = Depends(get_db)):
    try:
        pid = UUID(page_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid page ID") from err
    result = await db.execute(select(orm.Page).where(orm.Page.id == pid))
    page = result.scalar_one_or_none()
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")
    # partial update handled via request body in real impl
    return {
        "id": str(page.id),
        "title": page.title,
        "slug": page.slug,
        "content_markdown": page.content_markdown,
        "summary": page.summary,
        "source_name": None,
        "source_url": None,
        "source_video_url": None,
        "status": page.status,
        "created_at": str(page.created_at) if page.created_at else None,
        "updated_at": str(page.updated_at) if page.updated_at else None,
        "published_at": str(page.published_at) if page.published_at else None,
        "sections": [],
        "media_assets": [],
        "linked_pages": [],
    }


# ──────────────────────────────────────────
# Graph (page-link graph)
# ──────────────────────────────────────────


@router.get("/graph")
async def page_graph(
    source_id: str = None,
    limit: int = 100,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    q = (
        select(orm.PageLink, orm.Page, orm.Source)
        .join(orm.Page, orm.PageLink.from_page_id == orm.Page.id)
        .join(orm.Source, orm.Page.source_id == orm.Source.id, isouter=True)
    )
    if source_id:
        with contextlib.suppress(ValueError):
            q = q.where(orm.Page.source_id == UUID(source_id))
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    rows = result.all()

    node_ids = set()
    nodes = []
    edges = []
    for link, from_page, src in rows:
        if str(from_page.id) not in node_ids:
            node_ids.add(str(from_page.id))
            nodes.append(
                {
                    "id": str(from_page.id),
                    "title": from_page.title,
                    "source_name": src.name if src else None,
                }
            )
        edges.append(
            {
                "from": str(link.from_page_id),
                "to": str(link.to_page_id),
                "relation_type": link.relation_type,
            }
        )
    return {"nodes": nodes, "edges": edges}


# ──────────────────────────────────────────
# Entity Graph
# ──────────────────────────────────────────


async def _entity_event_counts(db: AsyncSession, entity_ids: list[UUID]) -> dict[UUID, int]:
    """Return a mapping of entity_id -> number of linked events."""
    if not entity_ids:
        return {}
    result = await db.execute(
        select(
            orm.EventEntityLink.entity_id,
            func.count(orm.EventEntityLink.event_id).label("cnt"),
        )
        .where(orm.EventEntityLink.entity_id.in_(entity_ids))
        .group_by(orm.EventEntityLink.entity_id)
    )
    return {row[0]: row[1] for row in result.all()}


def _entity_node_dict(e: orm.Entity, event_count: int = 0) -> dict:
    return {
        "id": str(e.id),
        "label": e.name,
        "type": e.type,
        "ticker": e.ticker,
        "event_count": event_count,
    }


def _entity_relation_dict(r: orm.EntityRelation) -> dict:
    return {
        "source": str(r.from_entity_id),
        "target": str(r.to_entity_id),
        "edge_type": "entity_relation",
        "predicate": r.predicate,
        "confidence": r.confidence,
    }


@router.get("/entity-graph")
async def entity_graph(
    entity_type: str | None = None,
    predicate: str | None = None,
    depth: int = 1,
    limit: int = 200,
    entity_id: str | None = None,
    full_graph: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """Return a subgraph of entities and their relations.

    - ``entity_id``: start from a specific entity and expand by ``depth``.
    - ``entity_type``: filter the result set by entity type.
    - ``full_graph=true``: return all entities up to ``limit`` (use sparingly).
    - ``predicate``: filter relations by predicate.
    """
    if full_graph == "true":
        limit = limit or 2000
        entities_q = select(orm.Entity).limit(limit)
        if entity_type:
            entities_q = entities_q.where(orm.Entity.type == entity_type)
        ent_result = await db.execute(entities_q)
        entities = ent_result.scalars().all()
        entity_ids = [e.id for e in entities]

        relations_q = select(orm.EntityRelation).where(
            orm.EntityRelation.from_entity_id.in_(entity_ids),
            orm.EntityRelation.to_entity_id.in_(entity_ids),
        )
        if predicate:
            relations_q = relations_q.where(orm.EntityRelation.predicate == predicate)
        relations_q = relations_q.limit(limit * 2)
        rel_result = await db.execute(relations_q)
        relations = rel_result.scalars().all()

        event_counts = await _entity_event_counts(db, entity_ids)
        nodes = [_entity_node_dict(e, event_counts.get(e.id, 0)) for e in entities]
        edges = [_entity_relation_dict(r) for r in relations]
        return {"nodes": nodes, "edges": edges}

    # Depth-based expansion around one or more seed entities.
    seed_ids: set[UUID] = set()
    if entity_id:
        try:
            seed_ids.add(UUID(entity_id))
        except ValueError as err:
            raise HTTPException(status_code=400, detail="Invalid entity_id") from err

    if entity_type and not seed_ids:
        # No seed: pick the most connected entities of the requested type.
        type_entities_q = (
            select(orm.Entity.id).where(orm.Entity.type == entity_type).limit(min(limit, 50))
        )
        type_result = await db.execute(type_entities_q)
        seed_ids.update(row[0] for row in type_result.all())

    if not seed_ids:
        # Fallback: most connected entities overall.
        popular_q = (
            select(orm.EntityRelation.from_entity_id)
            .group_by(orm.EntityRelation.from_entity_id)
            .order_by(func.count(orm.EntityRelation.from_entity_id).desc())
            .limit(min(limit, 50))
        )
        pop_result = await db.execute(popular_q)
        seed_ids.update(row[0] for row in pop_result.all())

    current_ids = seed_ids.copy()
    collected_ids = seed_ids.copy()
    current_depth = 0

    while current_ids and current_depth < max(1, depth):
        relations_q = select(orm.EntityRelation).where(
            orm.EntityRelation.from_entity_id.in_(current_ids)
            | orm.EntityRelation.to_entity_id.in_(current_ids)
        )
        if predicate:
            relations_q = relations_q.where(orm.EntityRelation.predicate == predicate)
        rel_result = await db.execute(relations_q)
        relations = rel_result.scalars().all()

        next_ids: set[UUID] = set()
        for r in relations:
            next_ids.add(r.from_entity_id)
            next_ids.add(r.to_entity_id)

        current_ids = next_ids - collected_ids
        collected_ids |= next_ids
        current_depth += 1

        if len(collected_ids) > limit:
            break

    # Fetch full entity rows and relations among the collected set.
    collected_list = list(collected_ids)[:limit]
    if not collected_list:
        return {"nodes": [], "edges": []}

    entities_q = select(orm.Entity).where(orm.Entity.id.in_(collected_list))
    if entity_type:
        entities_q = entities_q.where(orm.Entity.type == entity_type)
    ent_result = await db.execute(entities_q)
    entities = ent_result.scalars().all()

    entity_id_set = {e.id for e in entities}
    relations_q = select(orm.EntityRelation).where(
        orm.EntityRelation.from_entity_id.in_(entity_id_set),
        orm.EntityRelation.to_entity_id.in_(entity_id_set),
    )
    if predicate:
        relations_q = relations_q.where(orm.EntityRelation.predicate == predicate)
    rel_result = await db.execute(relations_q)
    relations = rel_result.scalars().all()

    event_counts = await _entity_event_counts(db, entity_id_set)
    nodes = [_entity_node_dict(e, event_counts.get(e.id, 0)) for e in entities]
    edges = [_entity_relation_dict(r) for r in relations]
    return {"nodes": nodes, "edges": edges}


# ──────────────────────────────────────────
# Cluster Graph
# ──────────────────────────────────────────

# ──────────────────────────────────────────
# Cluster Graph (entity-type hierarchy)
# ──────────────────────────────────────────

TYPE_COLORS: dict[str, str] = {
    "stock_ticker": "#3b82f6",
    "company": "#eab308",
    "sector": "#22c55e",
    "person": "#f97316",
    "bank": "#ef4444",
    "market_index": "#8b5cf6",
    "commodity": "#ec4899",
    "bond": "#14b8a6",
    "policy": "#64748b",
    "macro_indicator": "#06b6d4",
    "financial_metric": "#84cc16",
    "country": "#f43f5e",
    "city": "#0ea5e9",
    "organization": "#a855f7",
    "executive": "#d946ef",
    "fund": "#6366f1",
    "interest_rate": "#10b981",
    "monetary_policy": "#78716c",
    "trade_policy": "#fbbf24",
    "securities_firm": "#0891b2",
    "real_estate_project": "#f59e0b",
    "cryptocurrency": "#f97316",
    "precious_metal": "#d4a574",
    "energy": "#e11d48",
    "other": "#9ca3af",
}

_FALLBACK_COLORS = [
    "#64748b",
    "#94a3b8",
    "#a1a1aa",
    "#71717a",
    "#6b7280",
    "#78716c",
    "#a8a29e",
    "#78716c",
    "#9ca3af",
    "#b0b0b0",
]


@router.get("/cluster-graph")
async def cluster_graph(db: AsyncSession = Depends(get_db)):
    """Return entity-type clusters with aggregated inter-cluster relations.

    Mirrors the legacy 29_LLM_wiki graph API so the 3D cluster view renders
    colors, sizes, and edges consistently.
    """
    entity_rows = (
        await db.execute(
            text("""
        SELECT DISTINCT e.id, e.type
        FROM entities e
        WHERE EXISTS (
            SELECT 1 FROM entity_relations er
            WHERE er.from_entity_id = e.id OR er.to_entity_id = e.id
        )
    """)
        )
    ).all()

    entity_type_map: dict[str, str] = {str(row.id): row.type for row in entity_rows}

    type_counts: dict[str, int] = defaultdict(int)
    for etype in entity_type_map.values():
        type_counts[etype] += 1

    rel_rows = (
        await db.execute(
            text("""
        SELECT from_entity_id, to_entity_id, predicate
        FROM entity_relations
    """)
        )
    ).all()

    cluster_edges: dict[tuple[str, str, str], int] = defaultdict(int)
    for row in rel_rows:
        from_type = entity_type_map.get(str(row.from_entity_id))
        to_type = entity_type_map.get(str(row.to_entity_id))
        if from_type and to_type and from_type != to_type:
            key = (from_type, to_type, row.predicate)
            cluster_edges[key] += 1

    edge_counts: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for (from_t, to_t, pred), cnt in cluster_edges.items():
        edge_counts[(from_t, to_t)].append((pred, cnt))

    edges = []
    color_idx = 0
    for (from_t, to_t), pred_list in edge_counts.items():
        pred_list.sort(key=lambda x: -x[1])
        total = sum(c for _, c in pred_list)
        if total < 2:
            continue
        top_pred, top_cnt = pred_list[0]
        edges.append(
            {
                "source": from_t,
                "target": to_t,
                "predicate": f"{top_pred} ({top_cnt}/{total})",
                "relation_count": total,
            }
        )

    clusters = []
    for etype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        color = TYPE_COLORS.get(etype)
        if not color:
            color = _FALLBACK_COLORS[color_idx % len(_FALLBACK_COLORS)]
            color_idx += 1
        clusters.append(
            {
                "id": etype,
                "label": etype.replace("_", " ").title(),
                "entity_count": cnt,
                "color": color,
            }
        )

    return {"clusters": clusters, "edges": edges}


@router.get("/cluster-expand")
async def cluster_expand(
    entity_type: str | None = None, limit: int = 500, db: AsyncSession = Depends(get_db)
):
    """Expand an entity-type cluster into its member entities and intra-cluster relations."""
    q = select(orm.Entity)
    if entity_type:
        q = q.where(orm.Entity.type == entity_type)
    q = q.limit(limit)
    result = await db.execute(q)
    entities = result.scalars().all()
    entity_ids = [e.id for e in entities]
    entity_id_set = set(entity_ids)

    # Relations where both endpoints are within the returned entity set.
    relations_q = (
        select(orm.EntityRelation)
        .where(
            orm.EntityRelation.from_entity_id.in_(entity_id_set),
            orm.EntityRelation.to_entity_id.in_(entity_id_set),
        )
        .limit(limit * 2)
    )
    rel_result = await db.execute(relations_q)
    relations = rel_result.scalars().all()

    event_counts = await _entity_event_counts(db, entity_id_set)
    nodes = [_entity_node_dict(e, event_counts.get(e.id, 0)) for e in entities]
    edges = [_entity_relation_dict(r) for r in relations]
    return {"nodes": nodes, "edges": edges}


# ──────────────────────────────────────────
# Attention Items
# ──────────────────────────────────────────


@router.get("/attention-items")
async def attention_items(page: int = 1, per_page: int = 100, db: AsyncSession = Depends(get_db)):
    error_statuses = [
        "failed",
        "no_captions",
        "no_captions_t3_fail",
        "skipped",
        "requires_membership",
    ]
    q = (
        select(orm.SourceItem, orm.Source.name)
        .join(orm.Source)
        .where(orm.SourceItem.status.in_(error_statuses))
        .order_by(orm.SourceItem.created_at.desc())
        .limit(per_page)
        .offset((page - 1) * per_page)
    )
    result = await db.execute(q)
    items = [
        {
            "id": str(item.id),
            "video_id": item.external_id,
            "title": item.title,
            "status": item.status,
            "error_message": item.error_message,
            "source_name": src_name,
            "created_at": str(item.created_at) if item.created_at else None,
        }
        for item, src_name in result.all()
    ]
    return {"items": items, "total": len(items), "page": page, "per_page": per_page}


# ──────────────────────────────────────────
# Workers
# ──────────────────────────────────────────


@router.get("/workers")
async def list_workers(db: AsyncSession = Depends(get_db)):
    cutoff = now() - timedelta(minutes=5)

    # Delete stale rows (pod no longer exists or crashed > 5 min ago).
    # Only live workers remain after this point.
    await db.execute(
        delete(orm.WorkerHeartbeat).where(
            orm.WorkerHeartbeat.last_heartbeat < cutoff
        )
    )
    await db.commit()

    q = select(orm.WorkerHeartbeat).order_by(orm.WorkerHeartbeat.worker_id)
    result = await db.execute(q)
    workers = []
    for w in result.scalars():
        ago = (now() - w.last_heartbeat).total_seconds() if w.last_heartbeat else 999
        workers.append(
            {
                "worker_id": w.worker_id,
                "worker_type": w.worker_type,
                "status": w.status or "idle",
                "alive": ago < 120,
                "heartbeat_ago_secs": int(ago),
                "current_job_id": str(w.current_job_id) if w.current_job_id else None,
                "current_stage": w.current_stage,
                "stage_duration_secs": 0,
                "cpu_percent": w.cpu_percent or 0,
                "error_message": w.error_message,
            }
        )
    return {"workers": workers}


# ──────────────────────────────────────────
# Chat Sessions (implemented in chat_sessions.py)
# ──────────────────────────────────────────


async def _no_mutation_stub():
    raise HTTPException(status_code=501, detail="Not implemented — use the admin UI")
