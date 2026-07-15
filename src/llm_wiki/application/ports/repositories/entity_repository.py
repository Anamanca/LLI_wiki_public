from abc import ABC, abstractmethod
from typing import Optional

from llm_wiki.domain.entities.entity import Entity, EntityRelation, EventEntityLink


class EntityRepository(ABC):
    @abstractmethod
    async def get_by_id(self, entity_id: "EntityId") -> Optional[Entity]: ...

    @abstractmethod
    async def save(self, entity: Entity) -> Entity: ...

    @abstractmethod
    async def save_event_link(self, link: EventEntityLink) -> EventEntityLink: ...

    @abstractmethod
    async def save_relation(self, relation: EntityRelation) -> EntityRelation: ...

    @abstractmethod
    async def find_by_name_and_type(self, name: str, type: str) -> Optional[Entity]: ...

    @abstractmethod
    async def list_by_event(self, event_id: "EventId") -> list[Entity]: ...

    @abstractmethod
    async def list_by_type(self, entity_type: str, limit: int = 50) -> list[Entity]: ...

    @abstractmethod
    async def search_relations(
        self, entity_id: "EntityId", relation_type: Optional[str] = None, limit: int = 50
    ) -> list[EntityRelation]: ...
