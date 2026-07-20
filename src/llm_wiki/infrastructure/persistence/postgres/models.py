from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR, UUID
from sqlalchemy.orm import DeclarativeBase, deferred, relationship
from sqlalchemy.sql.schema import Computed, FetchedValue


def uuid7() -> str:
    import uuid as _uuid

    return str(_uuid.uuid4())


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    name = Column(String(255), nullable=False)
    platform = Column(String(50), nullable=False, default="youtube")
    external_id = Column(String(255), nullable=False)
    url = Column(String(512), nullable=False)
    added_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    last_checked_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="active")
    config = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))

    __table_args__ = (UniqueConstraint("platform", "external_id", name="uq_sources_platform_external"),)

    items = relationship("SourceItem", back_populates="source", cascade="all, delete-orphan")
    pages = relationship("Page", back_populates="source", cascade="all, delete-orphan")


class SourceItem(Base):
    __tablename__ = "source_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="CASCADE"), nullable=False)
    external_id = Column(String(255), nullable=False)
    title = Column(String(1024), nullable=True)
    url = Column(String(512), nullable=True)
    published_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="pending")
    started_at = Column(DateTime(timezone=True), nullable=True)
    retry_count = Column(Integer, nullable=False, server_default=text("0"))
    priority = Column(Integer, nullable=False, server_default=text("0"))
    retry_after = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(Text, nullable=True)
    transcript_text = Column(Text, nullable=True)
    transcript_json = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("source_id", "external_id", name="uq_source_items_source_external"),
        Index("ix_source_items_source_status", "source_id", "status"),
    )

    source = relationship("Source", back_populates="items")
    media_assets = relationship("MediaAsset", back_populates="source_item", cascade="all, delete-orphan")
    ingestion_logs = relationship("IngestionLog", back_populates="source_item", cascade="all, delete-orphan")


class Page(Base):
    __tablename__ = "pages"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    source_item_id = Column(UUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True)
    title = Column(String(1024), nullable=False)
    slug = Column(String(512), nullable=False, unique=True)
    content_markdown = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    domain = Column(String(100), nullable=True)
    key_entities = Column(ARRAY(String), nullable=True)
    summary_vector = Column(Vector(1024), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False)
    published_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(20), nullable=False, default="published")

    __table_args__ = (
        Index("ix_pages_source_id", "source_id"),
        Index("ix_pages_published_at", "published_at"),
        Index(
            "ix_pages_summary_vector_hnsw",
            "summary_vector",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"summary_vector": "vector_cosine_ops"},
        ),
    )

    source = relationship("Source", back_populates="pages")
    sections = relationship("PageSection", back_populates="page", cascade="all, delete-orphan")
    links_from = relationship("PageLink", back_populates="from_page", foreign_keys="PageLink.from_page_id", cascade="all, delete-orphan")
    links_to = relationship("PageLink", back_populates="to_page", foreign_keys="PageLink.to_page_id", cascade="all, delete-orphan")
    snapshots = relationship("PageSnapshot", back_populates="page", cascade="all, delete-orphan")
    media_assets = relationship("MediaAsset", back_populates="page")
    observations = relationship("EventObservation", back_populates="page", cascade="all, delete-orphan")


class PageSection(Base):
    __tablename__ = "page_sections"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    section_order = Column(Integer, nullable=False, default=0)
    title = Column(String(1024), nullable=True)
    content_markdown = Column(Text, nullable=True)
    section_vector = Column(Vector(1024), nullable=True)
    fts_vector = deferred(Column(TSVECTOR, Computed("to_tsvector('simple', coalesce(content_markdown, ''))", persisted=True), nullable=True))
    source_ref = Column(String(512), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        Index("ix_page_sections_page_id", "page_id"),
        Index("ix_page_sections_source_id", "source_id"),
        Index(
            "ix_page_sections_vector_hnsw",
            "section_vector",
            postgresql_using="hnsw",
            postgresql_with={"m": 16, "ef_construction": 200},
            postgresql_ops={"section_vector": "vector_cosine_ops"},
        ),
    )

    page = relationship("Page", back_populates="sections")


class PageLink(Base):
    __tablename__ = "page_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    from_page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    to_page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(50), nullable=False, default="related")
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        UniqueConstraint("from_page_id", "to_page_id", "relation_type", name="uq_page_links_from_to_relation"),
    )

    from_page = relationship("Page", back_populates="links_from", foreign_keys=[from_page_id])
    to_page = relationship("Page", back_populates="links_to", foreign_keys=[to_page_id])


class MediaAsset(Base):
    __tablename__ = "media_assets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    source_item_id = Column(UUID(as_uuid=True), ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL"), nullable=True)
    section_id = Column(UUID(as_uuid=True), ForeignKey("page_sections.id", ondelete="SET NULL"), nullable=True)
    filename = Column(String(512), nullable=False)
    minio_path = Column(String(1024), nullable=False)
    mime_type = Column(String(100), nullable=True)
    file_size_bytes = Column(BigInteger, nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (
        Index("ix_media_assets_source_item_id", "source_item_id"),
        Index("ix_media_assets_page_id", "page_id"),
    )

    source_item = relationship("SourceItem", back_populates="media_assets")
    page = relationship("Page", back_populates="media_assets")


class IngestionLog(Base):
    __tablename__ = "ingestion_logs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    source_item_id = Column(UUID(as_uuid=True), ForeignKey("source_items.id", ondelete="CASCADE"), nullable=False)
    event_type = Column(String(50), nullable=False)
    message = Column(Text, nullable=True)
    metadata_json = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (Index("ix_ingestion_logs_source_item_id", "source_item_id"),)

    source_item = relationship("SourceItem", back_populates="ingestion_logs")


class TelegramSubscriber(Base):
    __tablename__ = "telegram_subscribers"

    chat_id = Column(BigInteger, primary_key=True)
    username = Column(String(255), nullable=True)
    subscribed_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    is_active = Column(Boolean, nullable=False, server_default=text("true"))


class PageSnapshot(Base):
    __tablename__ = "page_snapshots"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="CASCADE"), nullable=False)
    source_item_id = Column(UUID(as_uuid=True), ForeignKey("source_items.id", ondelete="SET NULL"), nullable=True)
    content_markdown = Column(Text, nullable=True)
    sections_jsonb = Column(JSONB, nullable=False, server_default=text("'[]'::jsonb"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    __table_args__ = (UniqueConstraint("page_id", "source_item_id", name="uq_page_snapshots_page_item"),)

    page = relationship("Page", back_populates="snapshots")


class ScanLock(Base):
    __tablename__ = "scan_lock"

    scan_date = Column(Date, primary_key=True)
    started_at = Column(DateTime(timezone=True), nullable=False)
    completed_at = Column(DateTime(timezone=True), nullable=True)


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"

    worker_id = Column(Integer, primary_key=True)
    status = Column(String(20), nullable=False, default="idle")
    current_job_id = Column(UUID(as_uuid=True), nullable=True)
    current_stage = Column(String(50), nullable=True)
    stage_started_at = Column(DateTime(timezone=True), nullable=True)
    last_heartbeat = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    cpu_percent = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)


class EventCanonical(Base):
    __tablename__ = "event_canonicals"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    title = Column(Text, nullable=False)
    normalized_date = Column(Date, nullable=True)
    normalized_date_end = Column(Date, nullable=True)
    category = Column(String(50), nullable=True)
    entities = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    importance_score = Column(Float, nullable=False, server_default=text("0.0"))
    canonical_embedding = Column(Vector(1024), nullable=True)
    first_seen_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False)
    observation_count = Column(Integer, nullable=False, server_default=text("0"))
    consensus_summary = Column(Text, nullable=True)
    consensus_generated_at = Column(DateTime(timezone=True), nullable=True)

    observations = relationship("EventObservation", back_populates="event", cascade="all, delete-orphan")
    chains_from = relationship("EventTimelineChain", back_populates="from_event", foreign_keys="EventTimelineChain.from_event_id", cascade="all, delete-orphan")
    chains_to = relationship("EventTimelineChain", back_populates="to_event", foreign_keys="EventTimelineChain.to_event_id", cascade="all, delete-orphan")
    entity_links = relationship("EventEntityLink", back_populates="event", cascade="all, delete-orphan")

    __table_args__ = (
        Index("ix_event_canonicals_vector_hnsw", "canonical_embedding", postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 200}, postgresql_ops={"canonical_embedding": "vector_cosine_ops"}),
        Index("ix_event_canonicals_category_date", "category", "normalized_date"),
        Index("ix_event_canonicals_entities_gin", "entities", postgresql_using="gin", postgresql_ops={"entities": "jsonb_path_ops"}),
    )


class EventObservation(Base):
    __tablename__ = "event_observations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    event_id = Column(UUID(as_uuid=True), ForeignKey("event_canonicals.id", ondelete="CASCADE"), nullable=False)
    source_id = Column(UUID(as_uuid=True), ForeignKey("sources.id", ondelete="SET NULL"), nullable=True)
    page_id = Column(UUID(as_uuid=True), ForeignKey("pages.id", ondelete="SET NULL"), nullable=True)
    source_published_at = Column(DateTime(timezone=True), nullable=True)
    extracted_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    observation_type = Column(String(20), nullable=False, server_default=text("'initial_report'"))
    description = Column(Text, nullable=True)
    impact_direction = Column(String(255), nullable=True)
    metrics = Column(JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    confidence = Column(Float, nullable=False, server_default=text("0.5"))
    embedding = Column(Vector(1024), nullable=True)
    fts_vector = deferred(Column(TSVECTOR, Computed("to_tsvector('simple', coalesce(description, ''))", persisted=True), nullable=True))
    sentiment_score = Column(Float, nullable=True)
    stance = Column(String(20), nullable=True)

    event = relationship("EventCanonical", back_populates="observations")
    source = relationship("Source")
    page = relationship("Page", back_populates="observations")

    __table_args__ = (
        Index("ix_event_observations_vector_hnsw", "embedding", postgresql_using="hnsw", postgresql_with={"m": 16, "ef_construction": 200}, postgresql_ops={"embedding": "vector_cosine_ops"}),
        Index("ix_event_observations_published_at", "source_published_at"),
        Index("ix_event_observations_event_id", "event_id"),
        Index("ix_event_observations_page_id", "page_id"),
    )


class EventTimelineChain(Base):
    __tablename__ = "event_timeline_chains"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    from_event_id = Column(UUID(as_uuid=True), ForeignKey("event_canonicals.id", ondelete="CASCADE"), nullable=False)
    to_event_id = Column(UUID(as_uuid=True), ForeignKey("event_canonicals.id", ondelete="CASCADE"), nullable=False)
    relation_type = Column(String(30), nullable=False, default="causes")
    description = Column(Text, nullable=True)
    confidence = Column(Float, nullable=False, server_default=text("0.5"))
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    from_event = relationship("EventCanonical", back_populates="chains_from", foreign_keys=[from_event_id])
    to_event = relationship("EventCanonical", back_populates="chains_to", foreign_keys=[to_event_id])

    __table_args__ = (
        Index("ix_event_timeline_chains_from", "from_event_id", "relation_type"),
        Index("ix_event_timeline_chains_to", "to_event_id"),
        UniqueConstraint("from_event_id", "to_event_id", "relation_type", name="uq_event_chains_from_to_relation"),
    )


class Entity(Base):
    __tablename__ = "entities"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    name = Column(String(255), nullable=False)
    type = Column(String(30), nullable=False)
    canonical_name = Column(String(255), nullable=True)
    ticker = Column(String(20), nullable=True)
    extra = Column("metadata", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    first_seen_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    event_links = relationship("EventEntityLink", back_populates="entity", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("type", "canonical_name", name="uq_entities_type_canonical"),
        Index("ix_entities_type_name", "type", "name"),
        Index("ix_entities_ticker", "ticker", postgresql_where=text("ticker IS NOT NULL")),
    )


class EventEntityLink(Base):
    __tablename__ = "event_entity_links"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    event_id = Column(UUID(as_uuid=True), ForeignKey("event_canonicals.id", ondelete="CASCADE"), nullable=False)
    entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    relationship_type = Column(String(30), nullable=False, default="mentions")
    confidence = Column(Float, nullable=False, server_default=text("0.5"))
    extracted_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    event = relationship("EventCanonical", back_populates="entity_links")
    entity = relationship("Entity", back_populates="event_links")

    __table_args__ = (
        UniqueConstraint("event_id", "entity_id", "relationship_type", name="uq_event_entity_link"),
        Index("ix_event_entity_links_event", "event_id"),
        Index("ix_event_entity_links_entity", "entity_id"),
    )


class EntityRelation(Base):
    __tablename__ = "entity_relations"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    from_entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    to_entity_id = Column(UUID(as_uuid=True), ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    predicate = Column(String(50), nullable=False)
    properties = Column("properties", JSONB, nullable=False, server_default=text("'{}'::jsonb"))
    confidence = Column(Float, nullable=False, server_default=text("0.5"))
    source_event_id = Column(UUID(as_uuid=True), ForeignKey("event_canonicals.id", ondelete="SET NULL"), nullable=True)
    extracted_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)

    from_entity = relationship("Entity", foreign_keys=[from_entity_id])
    to_entity = relationship("Entity", foreign_keys=[to_entity_id])
    source_event = relationship("EventCanonical", foreign_keys=[source_event_id])

    __table_args__ = (
        UniqueConstraint("from_entity_id", "to_entity_id", "predicate", name="uq_entity_relations_from_to_predicate"),
        Index("ix_entity_relations_from_entity", "from_entity_id"),
        Index("ix_entity_relations_to_entity", "to_entity_id"),
        Index("ix_entity_relations_predicate", "predicate"),
    )


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid7, server_default=text("gen_random_uuid()"))
    provider = Column(String(50), nullable=False, default="opencode")
    api_key = Column(Text, nullable=False)
    model_name = Column(String(255), nullable=False, default="deepseek-v4-flash")
    status = Column(String(20), nullable=False, default="active")
    priority = Column(Integer, nullable=False, default=0)
    rate_limited_until = Column(DateTime(timezone=True), nullable=True)
    usage_count = Column(Integer, nullable=False, default=0)
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False)

    __table_args__ = (
        Index("ix_api_keys_status_priority", "status", "priority", "usage_count"),
        Index("ix_api_keys_rate_limited_until", "rate_limited_until"),
    )


class CronJob(Base):
    __tablename__ = "cron_jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(100), unique=True, nullable=False)
    name = Column(String(255), nullable=False)
    description = Column(Text, nullable=True)
    schedule = Column(String(100), nullable=False)
    job_type = Column(String(50), nullable=False, default="background_task")
    managed = Column(Boolean, nullable=False, default=True)
    enabled = Column(Boolean, nullable=False, default=True)
    command = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()"), nullable=False)
