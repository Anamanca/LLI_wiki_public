from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import SourceId


@dataclass
class Source:
    id: SourceId
    name: str
    platform: str = "youtube"
    external_id: str = ""
    url: str = ""
    added_at: datetime = field(default_factory=datetime.utcnow)
    last_checked_at: Optional[datetime] = None
    status: str = "active"
    config: dict = field(default_factory=dict)


@dataclass
class SourceItem:
    id: "SourceItemId"
    source_id: SourceId
    external_id: str
    title: Optional[str] = None
    url: Optional[str] = None
    published_at: Optional[datetime] = None
    status: str = "pending"
    started_at: Optional[datetime] = None
    retry_count: int = 0
    priority: int = 0
    retry_after: Optional[datetime] = None
    error_message: Optional[str] = None
    transcript_text: Optional[str] = None
    transcript_json: Optional[dict] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
