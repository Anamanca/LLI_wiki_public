from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class WorkerHeartbeat:
    worker_id: int
    status: str = "idle"
    current_job_id: Optional[str] = None
    current_stage: Optional[str] = None
    stage_started_at: Optional[datetime] = None
    last_heartbeat: datetime = field(default_factory=datetime.utcnow)
    cpu_percent: Optional[int] = None
    error_message: Optional[str] = None


@dataclass
class TelegramSubscriber:
    chat_id: int
    username: Optional[str] = None
    subscribed_at: datetime = field(default_factory=datetime.utcnow)
    is_active: bool = True


@dataclass
class ScanLock:
    scan_date: "date"
    started_at: datetime = field(default_factory=datetime.utcnow)
    completed_at: Optional[datetime] = None


@dataclass
class ApiKey:
    id: "ApiKeyId"
    provider: str = "opencode"
    api_key: str = ""
    model_name: str = "deepseek-v4-flash"
    status: str = "active"
    priority: int = 0
    rate_limited_until: Optional[datetime] = None
    usage_count: int = 0
    last_used_at: Optional[datetime] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class CronJob:
    id: int
    job_id: str
    name: str
    schedule: str
    description: Optional[str] = None
    job_type: str = "background_task"
    managed: bool = True
    enabled: bool = True
    command: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
