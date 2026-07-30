from dataclasses import dataclass, field
from datetime import UTC, datetime

from llm_wiki.domain.value_objects.identifiers import SourceItemId


@dataclass
class IngestionLog:
    id: "IngestionLogId"
    source_item_id: SourceItemId
    event_type: str
    message: str | None = None
    metadata_json: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
