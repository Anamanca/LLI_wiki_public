from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


@dataclass
class Entity:
    id: "EntityId"
    name: str
    type: str
    canonical_name: Optional[str] = None
    ticker: Optional[str] = None
    metadata: dict = field(default_factory=dict)
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EventEntityLink:
    id: "EventEntityLinkId"
    event_id: "EventId"
    entity_id: "EntityId"
    relationship_type: str = "mentions"
    confidence: float = 0.5
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class EntityRelation:
    id: "EntityRelationId"
    from_entity_id: "EntityId"
    to_entity_id: "EntityId"
    predicate: str
    properties: dict = field(default_factory=dict)
    confidence: float = 0.5
    source_event_id: Optional["EventId"] = None
    extracted_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
