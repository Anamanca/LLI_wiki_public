from dataclasses import dataclass
from typing import Optional


@dataclass
class IngestionInput:
    source_item_id: str
    force_reprocess: bool = False


@dataclass
class IngestionResult:
    source_item_id: str
    status: str
    page_slug: Optional[str] = None
    events_extracted: int = 0
    entities_linked: int = 0
    error_message: Optional[str] = None
