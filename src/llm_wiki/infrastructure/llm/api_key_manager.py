"""Multi-API-Key manager with lazy init, round-robin, auto-recovery, and 429 handling.

Singleton that loads active keys from DB every 30s. Falls back to .env config
if the DB has no active keys. Thread-safe via asyncio.Lock.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from datetime import datetime
from typing import Any

from sqlalchemy import select, update

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.models import ApiKey
from llm_wiki.shared.datetime_utils import get_system_tz, now

logger = logging.getLogger(__name__)

ProviderConfig = dict[str, str]


class ApiKeyManager:
    """Singleton manager for multi-API-key rotation with auto-switch on rate limits."""

    _instance: ApiKeyManager | None = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls) -> ApiKeyManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True
        self._keys: list[dict[str, Any]] = []
        self._cache_lock = asyncio.Lock()
        self._last_refresh: float = 0.0
        self._refresh_ttl: float = 30.0
        self._round_robin_idx: int = 0
        self._recovery_task: asyncio.Task[None] | None = None
        self._env_fallback_configured = False
        self._base_url_opencode = settings.opencode_base_url.rstrip("/")
        self._base_url_gemini = settings.gemini_base_url.rstrip("/")

    async def _ensure_cache(self) -> None:
        """Refresh the in-memory key cache if TTL has expired."""
        now = time.monotonic()
        if (now - self._last_refresh) < self._refresh_ttl:
            return
        async with self._cache_lock:
            now = time.monotonic()
            if (now - self._last_refresh) < self._refresh_ttl:
                return
            await self._refresh_cache()
            self._last_refresh = now

    async def _refresh_cache(self) -> None:
        """Load active + rate_limited keys from DB, sorted by priority/usage."""
        try:
            async with async_session_factory() as session:
                result = await session.execute(
                    select(ApiKey)
                    .where(ApiKey.status.in_(["active", "rate_limited"]))
                    .order_by(ApiKey.priority, ApiKey.usage_count)
                )
                rows = result.scalars().all()
                self._keys = [
                    {
                        "id": str(r.id),
                        "provider": r.provider,
                        "api_key": r.api_key,
                        "model_name": r.model_name,
                        "status": r.status,
                        "priority": r.priority,
                        "rate_limited_until": r.rate_limited_until,
                        "usage_count": r.usage_count,
                    }
                    for r in rows
                ]
                needs_seed = not self._keys and not self._env_fallback_configured

            if needs_seed:
                await self._seed_from_env()
                self._env_fallback_configured = True
            logger.debug("Refreshed API key cache: %d keys loaded", len(self._keys))
        except Exception:
            logger.exception("Failed to refresh API key cache")

    async def _seed_from_env(self) -> None:
        """Bootstrap the api_keys table from environment variables."""
        seed_keys: list[dict[str, str]] = []

        if settings.opencode_api_key:
            seed_keys.append(
                {
                    "provider": "opencode",
                    "api_key": settings.opencode_api_key,
                    "model_name": settings.opencode_primary_model,
                }
            )
        if settings.gemini_api_key:
            seed_keys.append(
                {
                    "provider": "gemini",
                    "api_key": settings.gemini_api_key,
                    "model_name": settings.gemini_primary_model,
                }
            )

        if not seed_keys:
            return

        async with async_session_factory() as session:
            for sk in seed_keys:
                key_hash = hashlib.sha256(sk["api_key"].encode()).hexdigest()[:16]
                existing = await session.execute(
                    select(ApiKey).where(ApiKey.provider == sk["provider"]).limit(1)
                )
                if existing.scalar_one_or_none() is None:
                    session.add(
                        ApiKey(
                            provider=sk["provider"],
                            api_key=sk["api_key"],
                            model_name=sk["model_name"],
                            status="active",
                            priority=0,
                        )
                    )
                    self._env_fallback_configured = True
                    logger.info(
                        "Seeded API key from env: provider=%s hash=%s", sk["provider"], key_hash
                    )

            await session.commit()

            result = await session.execute(
                select(ApiKey)
                .where(ApiKey.status.in_(["active", "rate_limited"]))
                .order_by(ApiKey.priority, ApiKey.usage_count)
            )
            rows = result.scalars().all()
            self._keys = [
                {
                    "id": str(r.id),
                    "provider": r.provider,
                    "api_key": r.api_key,
                    "model_name": r.model_name,
                    "status": r.status,
                    "priority": r.priority,
                    "rate_limited_until": r.rate_limited_until,
                    "usage_count": r.usage_count,
                }
                for r in rows
            ]

    async def get_next_key(self) -> ProviderConfig:
        """Return the next active API key config, rotating through available keys."""
        await self._ensure_cache()

        async with self._cache_lock:
            if self._recovery_task is None or self._recovery_task.done():
                self._recovery_task = asyncio.create_task(self._auto_recover_loop())
                logger.info("API key auto-recovery background task started")

        active_keys = [k for k in self._keys if k["status"] == "active" and k["api_key"]]

        if not active_keys:
            return self._env_fallback_config()

        n = len(active_keys)
        idx = self._round_robin_idx % n
        self._round_robin_idx = (self._round_robin_idx + 1) % n
        key = active_keys[idx]

        model = key["model_name"]
        fallback_model = model.replace("-pro", "-flash") if "-pro" in model else model
        chat_model = fallback_model

        return {
            "provider": key["provider"],
            "api_key": key["api_key"],
            "base_url": self._base_url_for(key["provider"]),
            "primary_model": model,
            "fallback_model": fallback_model,
            "chat_model": chat_model,
            "_key_id": key["id"],
        }

    def _base_url_for(self, provider: str) -> str:
        if provider == "gemini":
            return self._base_url_gemini
        return self._base_url_opencode

    def _env_fallback_config(self) -> ProviderConfig:
        """Fallback to env-based config when no DB keys are active."""
        if settings.opencode_api_key:
            return {
                "provider": "opencode",
                "api_key": settings.opencode_api_key,
                "base_url": self._base_url_opencode,
                "primary_model": settings.opencode_primary_model,
                "fallback_model": settings.opencode_fallback_model,
                "chat_model": settings.opencode_chat_model,
            }
        if settings.gemini_api_key:
            return {
                "provider": "gemini",
                "api_key": settings.gemini_api_key,
                "base_url": self._base_url_gemini,
                "primary_model": settings.gemini_primary_model,
                "fallback_model": settings.gemini_fallback_model,
                "chat_model": settings.gemini_chat_model,
            }

        raise RuntimeError(
            "No LLM API key configured. Set OPENCODE_API_KEY or GEMINI_API_KEY in .env"
        )

    async def get_active_config(self) -> ProviderConfig:
        """Return the currently active provider config without advancing round-robin."""
        await self._ensure_cache()

        active_keys = [k for k in self._keys if k["status"] == "active" and k["api_key"]]

        if not active_keys:
            return self._env_fallback_config()

        n = len(active_keys)
        idx = self._round_robin_idx % n
        key = active_keys[idx]

        model = key["model_name"]
        fallback_model = model.replace("-pro", "-flash") if "-pro" in model else model
        chat_model = fallback_model

        return {
            "provider": key["provider"],
            "api_key": key["api_key"],
            "base_url": self._base_url_for(key["provider"]),
            "primary_model": model,
            "fallback_model": fallback_model,
            "chat_model": chat_model,
        }

    async def mark_rate_limited(self, key_id: str, until: float) -> None:
        """Mark a key as rate-limited in both in-memory cache and DB."""
        until_dt = datetime.fromtimestamp(until, tz=get_system_tz())

        for k in self._keys:
            if k["id"] == key_id:
                k["status"] = "rate_limited"
                k["rate_limited_until"] = until
                logger.warning(
                    "Key %s marked rate-limited until %s", key_id[-8:], until_dt.isoformat()
                )
                break

        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == key_id)
                    .values(status="rate_limited", rate_limited_until=until_dt)
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to persist rate-limit mark for key %s", key_id[-8:])

    async def increment_usage(self, key_id: str | None) -> None:
        """Increment usage_count for a key after a successful call."""
        if not key_id:
            return

        now_ts = now()
        for k in self._keys:
            if k["id"] == key_id:
                k["usage_count"] = k.get("usage_count", 0) + 1
                break

        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == key_id)
                    .values(usage_count=ApiKey.usage_count + 1, last_used_at=now_ts)
                )
                await session.commit()
        except Exception:
            logger.exception("Failed to increment usage for key %s", key_id[-8:])

    def _ensure_recovery_running(self) -> None:
        """Start the auto-recovery background task if not already running."""
        if self._recovery_task is None or self._recovery_task.done():
            self._recovery_task = asyncio.create_task(self._auto_recover_loop())
            logger.info("API key auto-recovery background task started")

    async def _auto_recover_loop(self) -> None:
        """Periodically reactivate keys whose rate-limit cooldown has expired."""
        while True:
            try:
                await asyncio.sleep(30)
                now_ts = now()

                recovered = 0
                for k in self._keys:
                    if k["status"] == "rate_limited" and k.get("rate_limited_until"):
                        rlu = k["rate_limited_until"]
                        if isinstance(rlu, (int, float)) and rlu <= time.time():
                            k["status"] = "active"
                            k["rate_limited_until"] = None
                            recovered += 1
                            await self._reactivate_in_db(k["id"])

                async with async_session_factory() as session:
                    result = await session.execute(
                        select(ApiKey).where(
                            ApiKey.status == "rate_limited",
                            ApiKey.rate_limited_until <= now_ts,
                        )
                    )
                    stale = result.scalars().all()
                    for row in stale:
                        await self._reactivate_in_db(str(row.id))
                        for k in self._keys:
                            if k["id"] == str(row.id):
                                k["status"] = "active"
                                k["rate_limited_until"] = None
                        recovered += 1

                if recovered:
                    logger.info("Auto-recovered %d rate-limited keys", recovered)

            except asyncio.CancelledError:
                logger.info("API key auto-recovery task cancelled")
                return
            except Exception:
                logger.exception("Error in API key auto-recovery loop")
                await asyncio.sleep(60)

    async def _reactivate_in_db(self, key_id: str) -> None:
        """Set a key's status back to active and clear rate_limited_until."""
        try:
            async with async_session_factory() as session:
                await session.execute(
                    update(ApiKey)
                    .where(ApiKey.id == key_id)
                    .values(status="active", rate_limited_until=None)
                )
                await session.commit()
                logger.info("Reactivated key %s in DB", key_id[-8:])
        except Exception:
            logger.exception("Failed to reactivate key %s in DB", key_id[-8:])

    async def invalidate_cache(self) -> None:
        """Force a cache refresh on the next access by resetting the TTL."""
        self._last_refresh = 0.0
        logger.info("API key cache invalidated (will refresh on next access)")


_manager: ApiKeyManager | None = None


def get_key_manager() -> ApiKeyManager:
    """Return the singleton ApiKeyManager instance."""
    global _manager
    if _manager is None:
        _manager = ApiKeyManager()
    return _manager
