from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

from llm_wiki.domain.value_objects.identifiers import EventId, PageId, SourceId


@dataclass
class EventCanonical:
    id: EventId
    title: str
    normalized_date: Optional[date] = None
    normalized_date_end: Optional[date] = None
    category: Optional[str] = None
    entities: dict = field(default_factory=dict)
    importance_score: float = 0.0
    canonical_embedding: Optional[list[float]] = None
    first_seen_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    observation_count: int = 0
    consensus_summary: Optional[str] = None
    consensus_generated_at: Optional[datetime] = None


@dataclass
class EventObservation:
    id: "EventObservationId"
    event_id: EventId
    source_id: Optional[SourceId] = None
    page_id: Optional[PageId] = None
    source_published_at: Optional[datetime] = None
    extracted_at: datetime = field(default_factory=datetime.utcnow)
    observation_type: str = "initial_report"
    description: Optional[str] = None
    impact_direction: Optional[str] = None
    metrics: dict = field(default_factory=dict)
    confidence: float = 0.5
    embedding: Optional[list[float]] = None
    fts_vector: Optional[str] = None
    sentiment_score: Optional[float] = None
    stance: Optional[str] = None


@dataclass
class EventTimelineChain:
    id: "EventTimelineChainId"
    from_event_id: EventId
    to_event_id: EventId
    relation_type: str = "causes"
    description: Optional[str] = None
    confidence: float = 0.5
    created_at: datetime = field(default_factory=datetime.utcnow)
