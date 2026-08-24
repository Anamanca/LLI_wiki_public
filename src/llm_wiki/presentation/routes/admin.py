"""
Admin routes for ingestion, cron, scanning, and operational health.
Mounted unconditionally because the K8s CronJob depends on them.
"""

import logging
import os
from datetime import date, timedelta
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.use_cases.ingestion.youtube_poller import (
    PollResult,
    YouTubeQuotaExceeded,
    poll_channel,
    write_scan_log,
)
from llm_wiki.infrastructure.llm.api_key_manager import get_key_manager
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.persistence.redis.wiki_queue import push_wiki_job
from llm_wiki.presentation.dependencies import get_db
from llm_wiki.shared.datetime_utils import now

router = APIRouter(prefix="/admin")

_K8S_API_HOST = os.environ.get("KUBERNETES_SERVICE_HOST", "kubernetes.default.svc")
_K8S_API_PORT = os.environ.get("KUBERNETES_SERVICE_PORT", "443")
_K8S_NAMESPACE = os.environ.get("KUBERNETES_NAMESPACE", "llm-wiki")
_WORKER_HEARTBEAT_TIMEOUT_SECONDS = 60


def _today_utc() -> date:
    return now().date()


async def _prune_scan_logs(db: AsyncSession, retention_days: int = 10) -> int:
    """Delete scan_logs older than retention_days. Returns number of rows deleted."""
    cutoff = now() - timedelta(days=retention_days)
    result = await db.execute(delete(orm.ScanLog).where(orm.ScanLog.started_at < cutoff))
    await db.commit()
    deleted = result.rowcount
    if deleted:
        logger = logging.getLogger(__name__)
        logger.info("Pruned %d scan_logs older than %d days", deleted, retention_days)
    return deleted


async def _run_youtube_scan(
    db: AsyncSession,
    backfill: bool = False,
) -> dict:
    """Poll every active YouTube source and record the scan lock.

    This is the actual execution triggered by the daily cron job. It writes the
    scan_lock row so the GUI can report "done" for today, and it inserts any new
    videos as pending source_items for the workers to process.

    Each source gets an independent scan_logs row so quota burn and errors are
    auditable per channel. Old scan_logs rows (10+ days) are pruned at the start.
    """
    # Prune old scan logs before starting this run.
    await _prune_scan_logs(db)

    today = _today_utc()
    scan_date = today

    # Mark scan started (idempotent for the day).
    lock = await db.get(orm.ScanLock, scan_date)
    now_ts = now()
    if lock is None:
        lock = orm.ScanLock(scan_date=scan_date, started_at=now_ts)
        db.add(lock)
    else:
        lock.started_at = now_ts
    await db.commit()

    sources_result = await db.execute(
        select(orm.Source).where(
            orm.Source.platform == "youtube",
            orm.Source.status == "active",
        )
    )
    sources = sources_result.scalars().all()

    total_found = 0
    total_inserted = 0
    total_quota_used = 0
    total_api_calls = 0
    quota_exceeded = False
    errors: list[str] = []

    log = logging.getLogger(__name__)

    for source in sources:
        try:
            result = await poll_channel(source, db, backfill=backfill)
            total_found += result.found
            total_inserted += result.inserted
            total_quota_used += result.quota_used
            total_api_calls += result.api_calls
            if result.error:
                errors.append(f"{source.name}: {result.error}")
                if "quota" in (result.error or "").lower():
                    quota_exceeded = True
            # Write scan log regardless of outcome.
            await write_scan_log(
                db,
                str(source.id),
                source.name,
                scan_type="backfill" if backfill else "daily",
                result=result,
            )
        except YouTubeQuotaExceeded:
            quota_exceeded = True
            errors.append(f"quota exceeded for {source.name}")
            await write_scan_log(
                db,
                str(source.id),
                source.name,
                scan_type="backfill" if backfill else "daily",
                result=PollResult(error="YouTube daily quota exhausted"),
            )
            continue  # Don't break — other sources may still succeed
        except Exception as exc:
            log.exception("Daily scan failed for source %s", source.name)
            error_msg = str(exc)[:500]
            errors.append(f"{source.name}: {error_msg}")
            await write_scan_log(
                db,
                str(source.id),
                source.name,
                scan_type="backfill" if backfill else "daily",
                result=PollResult(error=error_msg),
            )
            continue

    lock.completed_at = now()
    await db.commit()

    return {
        "success": True,
        "message": "Daily YouTube scan completed",
        "scan_date": str(scan_date),
        "backfill": backfill,
        "sources_scanned": len(sources),
        "videos_found": total_found,
        "new_items": total_inserted,
        "quota_used": total_quota_used,
        "api_calls": total_api_calls,
        "quota_exceeded": quota_exceeded,
        "errors": errors,
    }


async def _scan_status(db: AsyncSession) -> dict:
    today = _today_utc()
    # All scan dates with a completed scan, in ascending order
    locks_q = select(orm.ScanLock).order_by(orm.ScanLock.scan_date)
    result = await db.execute(locks_q)
    locks = result.scalars().all()

    completed_dates = {lock.scan_date for lock in locks if lock.completed_at}
    last_scan_date = None
    last_scan_completed_at = None
    for lock in locks:
        if lock.completed_at:
            last_scan_date = lock.scan_date
            last_scan_completed_at = lock.completed_at

    # Missed dates = dates between the earliest completed scan and today that are not completed.
    if completed_dates:
        earliest = min(completed_dates)
        missed = []
        cursor = earliest
        while cursor <= today:
            if cursor != today and cursor not in completed_dates:
                missed.append(cursor)
            cursor += timedelta(days=1)
    else:
        missed = []

    pending_q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status.in_(["pending", "pending_transcribe"])
    )
    pending_count = (await db.execute(pending_q)).scalar() or 0

    req_mem_q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status == "requires_membership"
    )
    req_count = (await db.execute(req_mem_q)).scalar() or 0

    return {
        "last_scan_date": str(last_scan_date) if last_scan_date else None,
        "last_scan_completed_at": last_scan_completed_at.isoformat()
        if last_scan_completed_at
        else None,
        "missed_dates": [str(d) for d in missed],
        "pending_count": pending_count,
        "requires_membership_count": req_count,
        "scan_ok": (today - last_scan_date).days <= 1 if last_scan_date else False,
    }


def _k8s_api_client() -> httpx.AsyncClient:
    """Build an httpx client authorized to call the in-cluster Kubernetes API."""
    token_path = "/var/run/secrets/kubernetes.io/serviceaccount/token"
    ca_path = "/var/run/secrets/kubernetes.io/serviceaccount/ca.crt"
    headers = {}
    verify = ca_path if os.path.exists(ca_path) else False
    if os.path.exists(token_path):
        with open(token_path, encoding="utf-8") as f:
            headers["Authorization"] = f"Bearer {f.read().strip()}"
    return httpx.AsyncClient(
        base_url=f"https://{_K8S_API_HOST}:{_K8S_API_PORT}",
        headers=headers,
        verify=verify,
        timeout=httpx.Timeout(5.0),
    )


async def _list_k8s_cronjobs() -> dict[str, dict]:
    """Return a mapping of CronJob name -> metadata for jobs in the current namespace."""
    try:
        async with _k8s_api_client() as client:
            resp = await client.get(f"/apis/batch/v1/namespaces/{_K8S_NAMESPACE}/cronjobs")
            if resp.status_code != 200:
                return {}
            data = resp.json()
            return {
                item["metadata"]["name"]: {
                    "suspend": item.get("spec", {}).get("suspend", False),
                    "schedule": item.get("spec", {}).get("schedule", ""),
                }
                for item in data.get("items", [])
            }
    except Exception:
        return {}


async def _list_k8s_jobs() -> list[dict]:
    """Return recent Jobs in the current namespace sorted by creation time desc."""
    try:
        async with _k8s_api_client() as client:
            resp = await client.get(f"/apis/batch/v1/namespaces/{_K8S_NAMESPACE}/jobs")
            if resp.status_code != 200:
                return []
            data = resp.json()
            jobs = data.get("items", [])
            jobs.sort(
                key=lambda j: j.get("metadata", {}).get("creationTimestamp", ""),
                reverse=True,
            )
            return jobs
    except Exception:
        return []


async def _k8s_cronjob_status(cronjob_name: str) -> tuple[str, str | None]:
    """Return (status, last_run_iso) for a Kubernetes CronJob.

    Status values mirror the frontend badge mapping:
        scheduled  - CronJob exists and is not suspended
        stopped    - CronJob exists but is suspended
        running    - A child Job is currently active
        error      - The most recent completed Job failed
        not_found  - No matching CronJob exists in the namespace
    """
    cronjobs = await _list_k8s_cronjobs()
    if cronjob_name not in cronjobs:
        return "not_found", None

    spec = cronjobs[cronjob_name]
    if spec.get("suspend"):
        return "stopped", None

    jobs = await _list_k8s_jobs()
    own_jobs = [
        j
        for j in jobs
        if j.get("metadata", {}).get("ownerReferences", [{}])[0].get("name") == cronjob_name
    ]

    # Running takes precedence.
    for job in own_jobs:
        status = job.get("status", {})
        if status.get("active", 0) > 0:
            return "running", None

    # Look at the most recent completed/failed job for last-run and error state.
    for job in own_jobs:
        status = job.get("status", {})
        conditions = status.get("conditions", [])
        completion_time = status.get("completionTime")
        failed = any(c.get("type") == "Failed" and c.get("status") == "True" for c in conditions)
        if completion_time:
            last_run = completion_time
        elif conditions:
            last_run = conditions[0].get("lastTransitionTime")
        else:
            last_run = job.get("metadata", {}).get("creationTimestamp")
        if failed:
            return "error", last_run
        if completion_time:
            return "completed", last_run

    return "scheduled", None


async def _background_task_status(db: AsyncSession) -> tuple[str, int]:
    """Return (status, alive_workers) based on worker heartbeats.

    Status values:
        running    - At least one worker has heartbeat within timeout
        no_workers - No workers have reported recently
    """
    cutoff = now() - timedelta(seconds=_WORKER_HEARTBEAT_TIMEOUT_SECONDS)
    result = await db.execute(
        select(func.count(orm.WorkerHeartbeat.worker_id)).where(
            orm.WorkerHeartbeat.last_heartbeat >= cutoff
        )
    )
    alive = result.scalar() or 0
    return ("running" if alive > 0 else "no_workers"), alive


@router.get("/scan-status")
async def scan_status(db: AsyncSession = Depends(get_db)):
    """Read-only status of the daily scan pipeline."""
    return await _scan_status(db)


@router.get("/cron-jobs")
async def list_cron_jobs(db: AsyncSession = Depends(get_db)):
    q = select(orm.CronJob).order_by(orm.CronJob.job_id)
    result = await db.execute(q)
    rows = result.scalars().all()

    # Pre-fetch K8s state once for all kubernetes_cronjob entries.
    _ = await _list_k8s_cronjobs()
    _ = await _list_k8s_jobs()
    bg_status, bg_alive = await _background_task_status(db)

    jobs = []
    for j in rows:
        status: str
        last_run: str | None = None
        alive_workers: int | None = None
        crontab_active: bool | None = None
        error: str | None = None

        if not j.enabled:
            status = "stopped"
        elif j.job_type == "kubernetes_cronjob":
            status, last_run = await _k8s_cronjob_status(j.job_id)
            # If the daily scan lock for today is completed, report "completed"
            # so the GUI can show "Done Today" instead of just "Scheduled".
            if j.job_id == "youtube-daily-scan" and status == "scheduled":
                today = _today_utc()
                lock = await db.get(orm.ScanLock, today)
                if lock and lock.completed_at:
                    status = "completed"
        elif j.job_type == "crontab":
            # Crontab management is not implemented in this environment; treat as scheduled.
            status = "scheduled"
            crontab_active = True
        else:
            status = bg_status
            alive_workers = bg_alive

        jobs.append(
            {
                "job_id": j.job_id,
                "name": j.name,
                "description": j.description,
                "schedule": j.schedule,
                "job_type": j.job_type,
                "managed": j.managed,
                "status": status,
                "last_run": last_run,
                "crontab_active": crontab_active,
                "alive_workers": alive_workers,
                "error": error,
            }
        )

    return jobs


@router.post("/cron-jobs/{job_id}/start")
async def start_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(orm.CronJob).where(orm.CronJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.enabled = True
        await db.commit()

    # The daily YouTube scan is executed directly by this endpoint; the previous
    # forward-to-cpu-worker path was a no-op because no cpu-worker endpoint
    # implemented the scan. Running it here keeps the cron job synchronous and
    # lets the K8s job success/failure reflect the real outcome.
    if job_id == "youtube-daily-scan":
        status = await _scan_status(db)
        missed_dates = status.get("missed_dates", [])
        if missed_dates:
            return await _run_youtube_scan(
                db, backfill=True
            )
        return await _run_youtube_scan(db)

    return {"success": True, "message": f"Cron job {job_id} started"}


@router.post("/cron-jobs/{job_id}/stop")
async def stop_cron_job(job_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(orm.CronJob).where(orm.CronJob.job_id == job_id))
    job = result.scalar_one_or_none()
    if job:
        job.enabled = False
        await db.commit()
    return {"success": True, "message": f"Cron job {job_id} stopped"}


# ---------------------------------------------------------------------------
# Scan logs — audit trail for YouTube API usage per channel
# ---------------------------------------------------------------------------


@router.get("/scan-logs")
async def get_scan_logs(
    source_id: str | None = None,
    days: int = 7,
    limit: int = 100,
    db: AsyncSession = Depends(get_db),
):
    """Return recent scan log entries for analysis.

    Query params:
      source_id  — filter to a specific source (optional, returns all if omitted)
      days       — lookback window, default 7
      limit      — max rows, default 100
    """
    q = select(orm.ScanLog).order_by(orm.ScanLog.started_at.desc()).limit(limit)
    if source_id:
        try:
            sid = UUID(source_id)
        except ValueError as err:
            raise HTTPException(status_code=400, detail="Invalid source ID") from err
        q = q.where(orm.ScanLog.source_id == sid)
    if days > 0:
        cutoff = now() - timedelta(days=days)
        q = q.where(orm.ScanLog.started_at >= cutoff)

    result = await db.execute(q)
    logs = result.scalars().all()
    return [
        {
            "id": log.id,
            "source_id": str(log.source_id),
            "source_name": log.source_name,
            "scan_type": log.scan_type,
            "started_at": log.started_at.isoformat(),
            "completed_at": log.completed_at.isoformat() if log.completed_at else None,
            "api_calls": log.api_calls,
            "quota_used": log.quota_used,
            "videos_found": log.videos_found,
            "videos_inserted": log.videos_inserted,
            "error_message": log.error_message,
            "success": log.success,
        }
        for log in logs
    ]


@router.get("/health")
async def admin_health(db: AsyncSession = Depends(get_db)):
    """Operational health check used by alerts and dashboards."""
    status = await _scan_status(db)
    failed_q = select(func.count(orm.SourceItem.id)).where(orm.SourceItem.status == "failed")
    failed_count = (await db.execute(failed_q)).scalar() or 0
    status["failed_count"] = failed_count
    return status


# ──────────────────────────────────────────
# Simple operational helpers (read-only or safe mutations)
# ──────────────────────────────────────────


@router.post("/restart/{item_id}")
async def restart_item(item_id: str, db: AsyncSession = Depends(get_db)):
    try:
        iid = UUID(item_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid item ID") from err
    result = await db.execute(select(orm.SourceItem).where(orm.SourceItem.id == iid))
    item = result.scalar_one_or_none()
    if not item:
        raise HTTPException(status_code=404, detail="Item not found")
    # If item has already passed through cpu-worker (has transcript and classification),
    # reset to "classified" so cpu-worker skips re-extraction and wiki-consumer picks it up.
    # Otherwise, reset to "pending" so the full pipeline runs.
    if item.status == "wiki_processing" or (item.transcript_text and item.transcript_json):
        item.status = "classified"
    else:
        item.status = "pending"
    item.error_message = None
    item.retry_after = None
    # Explicit restart = fresh attempt: clear retry_count so the stale-recovery
    # sweeper (which permanently fails classified items with retry_count >= 10)
    # does not kill the re-queued job before the consumer can process it.
    item.retry_count = 0
    await db.commit()
    # If reset to "classified", push into Redis wiki queue so wiki-consumer picks it up.
    if item.status == "classified":
        try:
            await push_wiki_job(item.id)
        except Exception:
            logging.getLogger(__name__).exception(
                "Failed to push %s to wiki queue after restart", item.id
            )
    return {"status": "ok", "item_id": item_id, "restarted": 1, "new_status": item.status}


@router.post("/restart/source/{source_id}")
async def restart_source(source_id: str, db: AsyncSession = Depends(get_db)):
    try:
        sid = UUID(source_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid source ID") from err
    error_statuses = ["failed", "no_captions", "rate_limited", "skipped", "wiki_processing"]
    result = await db.execute(
        select(orm.SourceItem).where(
            orm.SourceItem.source_id == sid,
            orm.SourceItem.status.in_(error_statuses),
        )
    )
    items = result.scalars().all()
    count = 0
    for item in items:
        # wiki_processing items already have transcript → reset to classified, not pending
        item.status = "classified" if item.status == "wiki_processing" else "pending"
        item.error_message = None
        item.retry_after = None
        count += 1
    await db.commit()
    return {"status": "ok", "restarted": count}


# Also expose at the legacy /api/restart/source/{source_id} path for the admin UI.
@router.post("/api/restart/source/{source_id}")
async def restart_source_legacy(source_id: str, db: AsyncSession = Depends(get_db)):
    return await restart_source(source_id, db)


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------


class ApiKeyCreate(BaseModel):
    provider: str = Field(default="opencode", pattern="^(opencode|gemini)$")
    api_key: str = Field(min_length=8, max_length=512)
    model_name: str = Field(default="deepseek-v4-flash", max_length=255)
    priority: int = Field(default=0, ge=0, le=100)


class ApiKeyUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(active|rate_limited|disabled)$")
    priority: int | None = Field(default=None, ge=0, le=100)
    model_name: str | None = Field(default=None, max_length=255)


def _mask_key(api_key: str) -> str:
    """Mask an API key, showing only the trailing 4 characters."""
    return "***" + api_key[-4:] if len(api_key) >= 4 else "***"


def _serialize_key(r: orm.ApiKey) -> dict:
    return {
        "id": str(r.id),
        "provider": r.provider,
        "api_key_masked": _mask_key(r.api_key),
        "model_name": r.model_name,
        "status": r.status,
        "priority": r.priority,
        "rate_limited_until": r.rate_limited_until.isoformat()
        if r.rate_limited_until
        else None,
        "usage_count": r.usage_count,
        "last_used_at": r.last_used_at.isoformat() if r.last_used_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
    }


@router.get("/api-keys")
async def list_api_keys(db: AsyncSession = Depends(get_db)):
    q = select(orm.ApiKey).order_by(orm.ApiKey.priority, orm.ApiKey.created_at.desc())
    result = await db.execute(q)
    rows = result.scalars().all()
    return [_serialize_key(r) for r in rows]


@router.post("/api-keys", status_code=201)
async def create_api_key(payload: ApiKeyCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(
        select(orm.ApiKey).where(
            orm.ApiKey.provider == payload.provider,
            orm.ApiKey.api_key == payload.api_key,
        )
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="API key already exists for this provider")

    key = orm.ApiKey(
        provider=payload.provider,
        api_key=payload.api_key,
        model_name=payload.model_name,
        status="active",
        priority=payload.priority,
    )
    db.add(key)
    await db.commit()
    await db.refresh(key)

    await get_key_manager().invalidate_cache()

    logging.getLogger(__name__).info(
        "Created API key: provider=%s model=%s priority=%d",
        key.provider,
        key.model_name,
        key.priority,
    )
    return _serialize_key(key)


@router.put("/api-keys/{key_id}")
async def update_api_key(key_id: str, payload: ApiKeyUpdate, db: AsyncSession = Depends(get_db)):
    try:
        kid = UUID(key_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid API key ID") from err
    result = await db.execute(select(orm.ApiKey).where(orm.ApiKey.id == kid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    if payload.status is not None:
        key.status = payload.status
        if payload.status != "rate_limited":
            key.rate_limited_until = None
    if payload.priority is not None:
        key.priority = payload.priority
    if payload.model_name is not None:
        key.model_name = payload.model_name

    await db.commit()
    await db.refresh(key)

    await get_key_manager().invalidate_cache()

    return _serialize_key(key)


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        kid = UUID(key_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid API key ID") from err
    result = await db.execute(select(orm.ApiKey).where(orm.ApiKey.id == kid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")

    # Invariant: never delete the last *active* key, so at least one usable key
    # always remains (the env fallback is the safety net only if the DB is empty).
    if key.status == "active":
        active_count = (
            await db.execute(
                select(func.count(orm.ApiKey.id)).where(orm.ApiKey.status == "active")
            )
        ).scalar() or 0
        if active_count <= 1:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete the last active API key",
            )

    await db.delete(key)
    await db.commit()

    await get_key_manager().invalidate_cache()

    return {"status": "ok", "deleted": 1}


@router.post("/api-keys/{key_id}/activate")
async def activate_api_key(key_id: str, db: AsyncSession = Depends(get_db)):
    try:
        kid = UUID(key_id)
    except ValueError as err:
        raise HTTPException(status_code=400, detail="Invalid API key ID") from err
    result = await db.execute(select(orm.ApiKey).where(orm.ApiKey.id == kid))
    key = result.scalar_one_or_none()
    if not key:
        raise HTTPException(status_code=404, detail="API key not found")
    key.status = "active"
    key.rate_limited_until = None
    await db.commit()
    await db.refresh(key)

    await get_key_manager().invalidate_cache()

    return _serialize_key(key)


# ---------------------------------------------------------------------------
# Alerts
# ---------------------------------------------------------------------------


@router.delete("/clear-alerts")
async def clear_alerts(db: AsyncSession = Depends(get_db)):
    alert_types = ["error", "rate_limit", "retry", "api_limit", "api_key_error"]
    result = await db.execute(
        delete(orm.IngestionLog).where(orm.IngestionLog.event_type.in_(alert_types))
    )
    await db.commit()
    return {"status": "ok", "deleted": result.rowcount}
