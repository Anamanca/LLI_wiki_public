from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import PageId, SourceItemId


@dataclass
class MediaAsset:
    id: "MediaAssetId"
    source_item_id: SourceItemId
    filename: str
    minio_path: str
    page_id: Optional[PageId] = None
    section_id: Optional["PageSectionId"] = None
    mime_type: Optional[str] = None
    file_size_bytes: Optional[int] = None
    description: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
