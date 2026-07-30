from dataclasses import dataclass, field
from datetime import UTC, datetime

from llm_wiki.domain.value_objects.identifiers import PageId, SourceId, SourceItemId


@dataclass
class Page:
    id: PageId
    title: str
    slug: str
    source_id: SourceId | None = None
    source_item_id: SourceItemId | None = None
    content_markdown: str | None = None
    summary: str | None = None
    domain: str | None = None
    key_entities: list[str] | None = None
    summary_vector: list[float] | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    published_at: datetime | None = None
    status: str = "published"


@dataclass
class PageSection:
    id: "PageSectionId"
    page_id: PageId
    section_order: int = 0
    source_id: SourceId | None = None
    title: str | None = None
    content_markdown: str | None = None
    section_vector: list[float] | None = None
    fts_vector: str | None = None
    source_ref: str | None = None
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PageLink:
    id: "PageLinkId"
    from_page_id: PageId
    to_page_id: PageId
    relation_type: str = "related"
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class PageSnapshot:
    id: "PageSnapshotId"
    page_id: PageId
    source_item_id: SourceItemId | None = None
    content_markdown: str | None = None
    sections_jsonb: list = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
