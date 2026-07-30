from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import PageId, SourceItemId


@dataclass
class MediaAsset:
    id: "MediaAssetId"
    source_item_id: SourceItemId
    filename: str
    minio_path: str
    page_id: PageId | None = None
    section_id: Optional["PageSectionId"] = None
    mime_type: str | None = None
    file_size_bytes: int | None = None
    description: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
