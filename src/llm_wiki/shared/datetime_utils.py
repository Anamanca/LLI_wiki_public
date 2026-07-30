"""System-timezone-aware datetime helpers.

Reads the ``TZ`` environment variable (e.g. ``"Asia/Ho_Chi_Minh"``) and
returns timezone-aware datetimes in that zone.  Falls back to UTC when
``TZ`` is not set or invalid, so the behaviour is always safe.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_TZ_CACHE: timezone | None = None


def get_system_tz() -> timezone:
    """Return the timezone configured via the ``TZ`` environment variable.

    The result is cached after the first call.  If ``TZ`` is unset or
    invalid the function falls back to UTC and logs a warning.
    """
    global _TZ_CACHE
    if _TZ_CACHE is not None:
        return _TZ_CACHE

    tz_name = os.environ.get("TZ", "").strip()
    if not tz_name:
        _TZ_CACHE = timezone.utc
        return _TZ_CACHE

    try:
        # zoneinfo is stdlib since Python 3.9
        from zoneinfo import ZoneInfo

        _TZ_CACHE = ZoneInfo(tz_name)
    except Exception:
        logger.warning(
            "Invalid TZ=%r – falling back to UTC", tz_name, exc_info=True
        )
        _TZ_CACHE = timezone.utc

    return _TZ_CACHE


def now() -> datetime:
    """Return the current datetime in the configured system timezone.

    Equivalent to ``datetime.now(tz=get_system_tz())``.
    """
    return datetime.now(tz=get_system_tz())
