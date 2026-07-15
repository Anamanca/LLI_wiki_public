from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import SourceItemId


@dataclass
class IngestionLog:
    id: "IngestionLogId"
    source_item_id: SourceItemId
    event_type: str
    message: Optional[str] = None
    metadata_json: dict = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.utcnow)
