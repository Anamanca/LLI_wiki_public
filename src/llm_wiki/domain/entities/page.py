from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import PageId, SourceId, SourceItemId


@dataclass
class Page:
    id: PageId
    title: str
    slug: str
    source_id: Optional[SourceId] = None
    source_item_id: Optional[SourceItemId] = None
    content_markdown: Optional[str] = None
    summary: Optional[str] = None
    domain: Optional[str] = None
    key_entities: Optional[list[str]] = None
    summary_vector: Optional[list[float]] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    status: str = "published"


@dataclass
class PageSection:
    id: "PageSectionId"
    page_id: PageId
    section_order: int = 0
    source_id: Optional[SourceId] = None
    title: Optional[str] = None
    content_markdown: Optional[str] = None
    section_vector: Optional[list[float]] = None
    fts_vector: Optional[str] = None
    source_ref: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PageLink:
    id: "PageLinkId"
    from_page_id: PageId
    to_page_id: PageId
    relation_type: str = "related"
    created_at: datetime = field(default_factory=datetime.utcnow)


@dataclass
class PageSnapshot:
    id: "PageSnapshotId"
    page_id: PageId
    source_item_id: Optional[SourceItemId] = None
    content_markdown: Optional[str] = None
    sections_jsonb: list = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
