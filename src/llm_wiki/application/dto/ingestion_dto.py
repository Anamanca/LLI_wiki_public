from dataclasses import dataclass


@dataclass
class IngestionInput:
    source_item_id: str
    force_reprocess: bool = False


@dataclass
class IngestionResult:
    source_item_id: str
    status: str
    page_slug: str | None = None
    events_extracted: int = 0
    entities_linked: int = 0
    error_message: str | None = None
