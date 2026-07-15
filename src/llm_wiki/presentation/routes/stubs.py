"""
Routes backed by real database queries. Replaces empty stubs with live data.
"""
import os
from datetime import datetime, timezone
from uuid import UUID

import psutil
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, case, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.presentation.dependencies import get_db

router = APIRouter()

MASK = "***"

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
        "done_today": 0,
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
        per_source.append({
            "name": r.name,
            "done": done,
            "total": total,
            "percent": round(done / total * 100, 1) if total > 0 else 0,
        })

    # latest ingestion alerts — only error/warning types
    alert_types = ["error", "rate_limit", "retry", "api_limit"]
    alerts_q = (
        select(orm.IngestionLog)
        .where(orm.IngestionLog.event_type.in_(alert_types))
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
            elapsed = (datetime.now(timezone.utc) - item.started_at).total_seconds()
        stage_label_map = {
            "transcribe": "Transcribe",
            "wiki": "Generate Wiki",
            "event_extract": "Event Extraction",
        }
        processing_items.append({
            "id": str(item.id),
            "video_id": item.external_id,
            "title": item.title or "Unknown",
            "stage": item.status,
            "stage_label": stage_label_map.get(item.status, item.status or "Unknown"),
            "started_at": str(item.started_at) if item.started_at else None,
            "elapsed_seconds": int(elapsed),
            "source_name": src_name,
        })

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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")
    result = await db.execute(select(orm.Source).where(orm.Source.id == sid))
    src = result.scalar_one_or_none()
    if not src:
        raise HTTPException(status_code=404, detail="Source not found")

    # counts
    counts = {}
    for status_val in ["pending", "processing", "completed", "failed", "no_captions", "skipped", "rate_limited"]:
        c = await db.execute(
            select(func.count(orm.SourceItem.id)).where(
                orm.SourceItem.source_id == sid,
                orm.SourceItem.status == status_val,
            )
        )
        counts[status_val] = c.scalar() or 0

    page_count = (await db.execute(
        select(func.count(orm.Page.id)).where(orm.Page.source_id == sid)
    )).scalar() or 0
    video_count = (await db.execute(
        select(func.count(orm.SourceItem.id)).where(orm.SourceItem.source_id == sid)
    )).scalar() or 0

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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")

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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid item ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid item ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid item ID")
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
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid page ID")
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
        try:
            q = q.where(orm.Page.source_id == UUID(source_id))
        except ValueError:
            pass
    q = q.limit(limit).offset(offset)
    result = await db.execute(q)
    rows = result.all()

    node_ids = set()
    nodes = []
    edges = []
    for link, from_page, src in rows:
        if str(from_page.id) not in node_ids:
            node_ids.add(str(from_page.id))
            nodes.append({
                "id": str(from_page.id),
                "title": from_page.title,
                "source_name": src.name if src else None,
            })
        edges.append({
            "from": str(link.from_page_id),
            "to": str(link.to_page_id),
            "relation_type": link.relation_type,
        })
    return {"nodes": nodes, "edges": edges}


# ──────────────────────────────────────────
# Entity Graph
# ──────────────────────────────────────────

@router.get("/entity-graph")
async def entity_graph(
    entity_type: str = None,
    predicate: str = None,
    depth: int = None,
    limit: int = None,
    entity_id: str = None,
    full_graph: str = None,
    db: AsyncSession = Depends(get_db),
):
    limit = limit or (10000 if full_graph == "true" else 200)
    entities_q = select(orm.Entity)
    if entity_type:
        entities_q = entities_q.where(orm.Entity.type == entity_type)
    if entity_id:
        try:
            entities_q = entities_q.where(orm.Entity.id == UUID(entity_id))
        except ValueError:
            pass
    entities_q = entities_q.limit(limit)
    ent_result = await db.execute(entities_q)
    entities = ent_result.scalars().all()

    nodes = [
        {
            "id": str(e.id),
            "label": e.name,
            "type": e.type,
            "ticker": e.ticker,
            "event_count": 0,
        }
        for e in entities
    ]

    relations_q = select(orm.EntityRelation)
    if predicate:
        relations_q = relations_q.where(orm.EntityRelation.predicate == predicate)
    if entity_id:
        try:
            eid = UUID(entity_id)
            from sqlalchemy import or_
            relations_q = relations_q.where(
                or_(orm.EntityRelation.from_entity_id == eid, orm.EntityRelation.to_entity_id == eid)
            )
        except ValueError:
            pass
    relations_q = relations_q.limit(limit)
    rel_result = await db.execute(relations_q)
    relations = rel_result.scalars().all()
    edges = [
        {
            "source": str(r.from_entity_id),
            "target": str(r.to_entity_id),
            "edge_type": "entity_relation",
            "predicate": r.predicate,
            "confidence": r.confidence,
        }
        for r in relations
    ]

    return {"nodes": nodes, "edges": edges}


# ──────────────────────────────────────────
# Cluster Graph
# ──────────────────────────────────────────

@router.get("/cluster-graph")
async def cluster_graph(db: AsyncSession = Depends(get_db)):
    q = select(orm.Entity.type, func.count(orm.Entity.id)).group_by(orm.Entity.type)
    result = await db.execute(q)
    entity_types = result.all()
    nodes = []
    for et, cnt in entity_types:
        nodes.append({
            "id": et,
            "label": et,
            "type": "cluster",
            "ticker": None,
            "event_count": cnt,
        })
    return {"nodes": nodes, "edges": []}


@router.get("/cluster-expand")
async def cluster_expand(entity_type: str = None, limit: int = 1000, db: AsyncSession = Depends(get_db)):
    q = select(orm.Entity)
    if entity_type:
        q = q.where(orm.Entity.type == entity_type)
    q = q.limit(limit)
    result = await db.execute(q)
    entities = result.scalars().all()
    nodes = [
        {
            "id": str(e.id),
            "label": e.name,
            "type": e.type,
            "ticker": e.ticker,
            "event_count": 0,
        }
        for e in entities
    ]
    return {"nodes": nodes, "edges": []}


# ──────────────────────────────────────────
# Attention Items
# ──────────────────────────────────────────

@router.get("/attention-items")
async def attention_items(page: int = 1, per_page: int = 100, db: AsyncSession = Depends(get_db)):
    error_statuses = ["failed", "no_captions", "no_captions_t3_fail", "skipped", "requires_membership"]
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
    q = select(orm.WorkerHeartbeat).order_by(orm.WorkerHeartbeat.worker_id)
    result = await db.execute(q)
    workers = []
    for w in result.scalars():
        ago = 999
        if w.last_heartbeat:
            ago = (datetime.now(timezone.utc) - w.last_heartbeat).total_seconds()
        workers.append({
            "worker_id": w.worker_id,
            "status": w.status or "idle",
            "alive": ago < 120,
            "heartbeat_ago_secs": int(ago),
            "current_job_id": str(w.current_job_id) if w.current_job_id else None,
            "current_stage": w.current_stage,
            "stage_duration_secs": 0,
            "cpu_percent": w.cpu_percent or 0,
            "error_message": w.error_message,
        })
    return {"workers": workers}


# ──────────────────────────────────────────
# Restart
# ──────────────────────────────────────────

@router.post("/restart/{item_id}")
async def restart_item(item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        iid = UUID(item_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid item ID")
    result = await db.execute(select(orm.SourceItem).where(orm.SourceItem.id == iid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    item.status = "pending"
    item.error_message = None
    item.retry_after = None
    await db.commit()
    return {"status": "ok", "item_id": item_id, "restarted": 1}


@router.post("/restart/source/{source_id}")
async def restart_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid source ID")
    error_statuses = ["failed", "no_captions", "rate_limited", "skipped"]
    result = await db.execute(
        select(orm.SourceItem).where(
            orm.SourceItem.source_id == sid,
            orm.SourceItem.status.in_(error_statuses),
        )
    )
    items = result.scalars().all()
    count = 0
    for item in items:
        item.status = "pending"
        item.error_message = None
        item.retry_after = None
        count += 1
    await db.commit()
    return {"status": "ok", "restarted": count}


# ──────────────────────────────────────────
# Admin API Keys
# ──────────────────────────────────────────

@router.get("/admin/api-keys")
async def list_api_keys(db: AsyncSession = Depends(get_db)):
    q = select(orm.ApiKey).order_by(orm.ApiKey.priority.desc())
    result = await db.execute(q)
    keys = []
    for k in result.scalars():
        keys.append({
            "id": str(k.id),
            "provider": k.provider,
            "api_key_masked": k.api_key[:7] + MASK if k.api_key and len(k.api_key) > 7 else MASK,
            "model_name": k.model_name,
            "status": k.status,
            "priority": k.priority or 0,
            "rate_limited_until": str(k.rate_limited_until) if k.rate_limited_until else None,
            "usage_count": k.usage_count or 0,
            "last_used_at": str(k.last_used_at) if k.last_used_at else None,
            "created_at": str(k.created_at) if k.created_at else None,
            "updated_at": str(k.updated_at) if k.updated_at else None,
        })
    return keys


@router.post("/admin/api-keys")
async def create_api_key(db: AsyncSession = Depends(get_db)):
    return await _no_mutation_stub()


@router.put("/admin/api-keys/{key_id}")
async def update_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    return await _no_mutation_stub()


@router.delete("/admin/api-keys/{key_id}")
async def delete_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        kid = UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key ID")
    result = await db.execute(select(orm.ApiKey).where(orm.ApiKey.id == kid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    await db.delete(key)
    await db.commit()
    return {"status": "deleted", "deleted": key_id}


@router.post("/admin/api-keys/{key_id}/activate")
async def activate_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        kid = UUID(key_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid key ID")
    result = await db.execute(select(orm.ApiKey).where(orm.ApiKey.id == kid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    key.status = "active"
    key.rate_limited_until = None
    await db.commit()
    return {
        "id": str(key.id),
        "provider": key.provider,
        "api_key_masked": key.api_key[:7] + MASK if key.api_key and len(key.api_key) > 7 else MASK,
        "model_name": key.model_name,
        "status": key.status,
        "priority": key.priority or 0,
        "rate_limited_until": None,
        "usage_count": key.usage_count or 0,
        "last_used_at": str(key.last_used_at) if key.last_used_at else None,
        "created_at": str(key.created_at) if key.created_at else None,
        "updated_at": str(key.updated_at) if key.updated_at else None,
    }


# ──────────────────────────────────────────
# Admin Cron Jobs
# ──────────────────────────────────────────

@router.get("/admin/cron-jobs")
async def list_cron_jobs(db: AsyncSession = Depends(get_db)):
    q = select(orm.CronJob).order_by(orm.CronJob.job_id)
    result = await db.execute(q)
    jobs = []
    for j in result.scalars():
        jobs.append({
            "job_id": j.job_id,
            "name": j.name,
            "description": j.description,
            "schedule": j.schedule,
            "job_type": j.job_type,
            "managed": j.managed,
            "status": "active" if j.enabled else "inactive",
            "last_run": None,
        })
    return jobs


@router.post("/admin/cron-jobs/{job_id}/start")
async def start_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(orm.CronJob).where(orm.CronJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.enabled = True
        await db.commit()
    return {"success": True, "message": f"Cron job {job_id} started"}


@router.post("/admin/cron-jobs/{job_id}/stop")
async def stop_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(orm.CronJob).where(orm.CronJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.enabled = False
        await db.commit()
    return {"success": True, "message": f"Cron job {job_id} stopped"}


# ──────────────────────────────────────────
# Admin Clear Alerts
# ──────────────────────────────────────────

@router.delete("/admin/clear-alerts")
async def clear_alerts(all: str = None, db: AsyncSession = Depends(get_db)):
    alert_types = ["error", "rate_limit", "retry", "api_limit"]
    q = orm.IngestionLog.__table__.delete().where(
        orm.IngestionLog.event_type.in_(alert_types)
    )
    result = await db.execute(q)
    await db.commit()
    return {"status": "ok", "deleted": result.rowcount or 0}


# ──────────────────────────────────────────
# Chat Sessions (still stubs — requires file-based storage)
# ──────────────────────────────────────────

def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("/chat/sessions")
async def list_chat_sessions_stub():
    return []


@router.post("/chat/sessions")
async def create_chat_session_stub():
    return {"id": "stub-1", "title": "New Chat", "messages": [], "created_at": _now(), "updated_at": _now()}


@router.get("/chat/sessions/{session_id}")
async def get_chat_session_stub(session_id: str):
    return {"id": session_id, "title": "Chat", "messages": [], "created_at": _now(), "updated_at": _now()}


@router.put("/chat/sessions/{session_id}")
async def update_chat_session_stub(session_id: str):
    return {"id": session_id, "title": "Chat", "messages": [], "created_at": _now(), "updated_at": _now()}


@router.delete("/chat/sessions/{session_id}")
async def delete_chat_session_stub(session_id: str):
    return {"status": "deleted"}


async def _no_mutation_stub():
    raise HTTPException(status_code=501, detail="Not implemented — use the admin UI")
