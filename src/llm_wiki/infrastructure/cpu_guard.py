"""CPU-based admission control — pause worker if system CPU exceeds threshold."""

from __future__ import annotations

import asyncio
import logging
import time

try:
    import psutil
except ImportError:
    psutil = None  # type: ignore[assignment]

from llm_wiki.config import settings

logger = logging.getLogger(__name__)

CPU_MAX_PERCENT = settings.cpu_max_percent
CPU_CHECK_INTERVAL = 30  # seconds to sleep when CPU is over threshold

_last_check_time: float = 0.0
_last_check_result: bool = True


async def cpu_safe_to_proceed() -> bool:
    """Check if system CPU usage is below the threshold.

    Caches the result for 10 seconds to avoid hammering psutil.
    Returns True if safe to claim a job, False if the worker should pause.
    """
    global _last_check_time, _last_check_result

    now = time.time()
    if now - _last_check_time < 10.0:
        return _last_check_result

    if psutil is None:
        _last_check_result = True
        _last_check_time = now
        return True

    try:
        cpu = psutil.cpu_percent(interval=1)
    except Exception:
        cpu = 0.0

    _last_check_time = time.time()
    _last_check_result = cpu <= CPU_MAX_PERCENT

    if not _last_check_result:
        logger.warning(
            "CPU %.1f%% > %d%% threshold — pausing worker for %ds",
            cpu,
            CPU_MAX_PERCENT,
            CPU_CHECK_INTERVAL,
        )

    return _last_check_result


def get_current_cpu() -> float:
    """Return current system CPU usage percentage (non-blocking, cached)."""
    global _last_check_time, _last_check_result
    if psutil is None:
        return 0.0
    try:
        return psutil.cpu_percent(interval=0) or 0.0
    except Exception:
        return 0.0
