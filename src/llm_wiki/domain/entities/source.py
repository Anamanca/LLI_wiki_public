from dataclasses import dataclass, field
from datetime import UTC, datetime

from llm_wiki.domain.value_objects.identifiers import SourceId


@dataclass
class Source:
    id: SourceId
    name: str
    platform: str = "youtube"
    external_id: str = ""
    url: str = ""
    added_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_checked_at: datetime | None = None
    last_video_published_at: datetime | None = None
    status: str = "active"
    config: dict = field(default_factory=dict)


@dataclass
class SourceItem:
    id: "SourceItemId"
    source_id: SourceId
    external_id: str
    title: str | None = None
    url: str | None = None
    published_at: datetime | None = None
    status: str = "pending"
    started_at: datetime | None = None
    retry_count: int = 0
    priority: int = 0
    retry_after: datetime | None = None
    error_message: str | None = None
    transcript_text: str | None = None
    transcript_json: dict | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
