from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from uuid import UUID


@dataclass
class WorkerHeartbeat:
    worker_id: int
    status: str = "idle"
    current_job_id: str | None = None
    current_stage: str | None = None
    stage_started_at: datetime | None = None
    last_heartbeat: datetime = field(default_factory=lambda: datetime.now(UTC))
    cpu_percent: int | None = None
    error_message: str | None = None


@dataclass
class TelegramSubscriber:
    chat_id: int
    username: str | None = None
    subscribed_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    is_active: bool = True


@dataclass
class ScanLock:
    scan_date: "date"
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None


ApiKeyId = UUID


@dataclass
class ApiKey:
    id: "ApiKeyId"
    provider: str = "opencode"
    api_key: str = ""
    model_name: str = "deepseek-v4-flash"
    status: str = "active"
    priority: int = 0
    rate_limited_until: datetime | None = None
    usage_count: int = 0
    last_used_at: datetime | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
