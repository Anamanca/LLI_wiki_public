from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Optional
from uuid import UUID

from llm_wiki.domain.value_objects.identifiers import EventId

EntityId = UUID
EventEntityLinkId = UUID
EntityRelationId = UUID


@dataclass
class Entity:
    id: "EntityId"
    name: str
    type: str
    canonical_name: str | None = None
    ticker: str | None = None
    metadata: dict = field(default_factory=dict)
    first_seen_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EventEntityLink:
    id: "EventEntityLinkId"
    event_id: "EventId"
    entity_id: "EntityId"
    relationship_type: str = "mentions"
    confidence: float = 0.5
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass
class EntityRelation:
    id: "EntityRelationId"
    from_entity_id: "EntityId"
    to_entity_id: "EntityId"
    predicate: str
    properties: dict = field(default_factory=dict)
    confidence: float = 0.5
    source_event_id: Optional["EventId"] = None
    extracted_at: datetime = field(default_factory=lambda: datetime.now(UTC))
