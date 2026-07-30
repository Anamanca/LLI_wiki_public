"""Worker heartbeat — periodic DB write for liveness monitoring.

Each worker writes a heartbeat row every 15 seconds with:
  - current status (idle/extracting/classifying/vision/embedding/wiki/error)
  - current job ID and stage
  - CPU usage percentage
  - error message (if any)
"""

from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy import text

from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.shared.datetime_utils import now

logger = logging.getLogger(__name__)

HEARTBEAT_INTERVAL = 15  # seconds


async def write_heartbeat(
    worker_id: int,
    status: str = "idle",
    current_job_id: UUID | None = None,
    current_stage: str | None = None,
    cpu_percent: int | None = None,
    error_message: str | None = None,
) -> None:
    """Upsert a heartbeat row for this worker."""
    try:
        async with async_session_factory() as db:
            await db.execute(
                text(
                    """
                    INSERT INTO worker_heartbeats (worker_id, status, current_job_id,
                        current_stage, stage_started_at, last_heartbeat, cpu_percent, error_message)
                    VALUES (:worker_id, :status, :job_id, :stage, :now, :now, :cpu, :error)
                    ON CONFLICT (worker_id) DO UPDATE SET
                        status = EXCLUDED.status,
                        current_job_id = EXCLUDED.current_job_id,
                        current_stage = EXCLUDED.current_stage,
                        stage_started_at = CASE
                            WHEN worker_heartbeats.current_stage IS DISTINCT FROM EXCLUDED.current_stage
                            THEN :now
                            ELSE worker_heartbeats.stage_started_at
                        END,
                        last_heartbeat = :now,
                        cpu_percent = EXCLUDED.cpu_percent,
                        error_message = EXCLUDED.error_message
                    """
                ),
                {
                    "worker_id": worker_id,
                    "status": status,
                    "job_id": str(current_job_id) if current_job_id else None,
                    "stage": current_stage,
                    "now": now(),
                    "cpu": cpu_percent,
                    "error": error_message[:1000] if error_message else None,
                },
            )
            await db.commit()
    except Exception as exc:
        logger.warning("Heartbeat write failed for worker-%d: %s", worker_id, exc)


# Module-level state updated by worker_loop for the heartbeat task to read.
_worker_state: dict[int, dict] = {}


def set_worker_state(
    worker_id: int,
    status: str,
    job_id: UUID | None = None,
    stage: str | None = None,
    cpu: int = 0,
    error: str | None = None,
) -> None:
    """Update the module-level state that the heartbeat background task reads."""
    _worker_state[worker_id] = {
        "status": status,
        "job_id": job_id,
        "stage": stage,
        "cpu": cpu,
        "error": error,
    }


def get_worker_state(worker_id: int) -> dict:
    """Read current worker state for the heartbeat task."""
    return _worker_state.get(worker_id, {})
