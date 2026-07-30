from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from llm_wiki.domain.value_objects.identifiers import EventId, PageId, SourceId


@dataclass
class EventCanonical:
    id: EventId
    title: str
    normalized_date: date | None = None
    normalized_date_end: date | None = None
    category: str | None = None
    entities: dict = field(default_factory=dict)
    importance_score: float = 0.0
    canonical_embedding: list[float] | None = None
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    observation_count: int = 0
    consensus_summary: str | None = None
    consensus_generated_at: datetime | None = None


@dataclass
class EventObservation:
    id: "EventObservationId"
    event_id: EventId
    source_id: SourceId | None = None
    page_id: PageId | None = None
    source_published_at: datetime | None = None
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    observation_type: str = "initial_report"
    description: str | None = None
    impact_direction: str | None = None
    metrics: dict = field(default_factory=dict)
    confidence: float = 0.5
    embedding: list[float] | None = None
    fts_vector: str | None = None
    sentiment_score: float | None = None
    stance: str | None = None


@dataclass
class EventTimelineChain:
    id: "EventTimelineChainId"
    from_event_id: EventId
    to_event_id: EventId
    relation_type: str = "causes"
    description: str | None = None
    confidence: float = 0.5
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
