from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.entity_repository import EntityRepository
from llm_wiki.domain.entities.entity import Entity, EntityRelation, EventEntityLink
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.persistence.postgres.mappers import (
    EntityMapper,
    EntityRelationMapper,
    EventEntityLinkMapper,
)


class PostgresEntityRepository(EntityRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, entity_id: "EntityId") -> Entity | None:
        result = await self._session.execute(
            select(orm.Entity).where(orm.Entity.id == entity_id.value)
        )
        row = result.scalar_one_or_none()
        return EntityMapper.to_domain(row) if row else None

    async def save(self, entity: Entity) -> Entity:
        existing = await self._session.get(orm.Entity, entity.id.value)
        orm_entity = EntityMapper.to_orm(entity, existing)
        self._session.add(orm_entity)
        await self._session.flush()
        return EntityMapper.to_domain(orm_entity)

    async def save_event_link(self, link: EventEntityLink) -> EventEntityLink:
        existing = await self._session.get(orm.EventEntityLink, link.id.value)
        orm_link = EventEntityLinkMapper.to_orm(link, existing)
        self._session.add(orm_link)
        await self._session.flush()
        return EventEntityLinkMapper.to_domain(orm_link)

    async def save_relation(self, relation: EntityRelation) -> EntityRelation:
        existing = await self._session.get(orm.EntityRelation, relation.id.value)
        orm_rel = EntityRelationMapper.to_orm(relation, existing)
        self._session.add(orm_rel)
        await self._session.flush()
        return EntityRelationMapper.to_domain(orm_rel)

    async def find_by_name_and_type(self, name: str, type: str) -> Entity | None:
        result = await self._session.execute(
            select(orm.Entity).where(
                orm.Entity.name == name,
                orm.Entity.type == type,
            )
        )
        row = result.scalar_one_or_none()
        return EntityMapper.to_domain(row) if row else None

    async def list_by_event(self, event_id: "EventId") -> list[Entity]:
        result = await self._session.execute(
            select(orm.Entity)
            .join(orm.EventEntityLink, orm.EventEntityLink.entity_id == orm.Entity.id)
            .where(orm.EventEntityLink.event_id == event_id.value)
        )
        return [EntityMapper.to_domain(r) for r in result.scalars()]

    async def list_by_type(self, entity_type: str, limit: int = 50) -> list[Entity]:
        result = await self._session.execute(
            select(orm.Entity).where(orm.Entity.type == entity_type).limit(limit)
        )
        return [EntityMapper.to_domain(r) for r in result.scalars()]

    async def search_relations(
        self, entity_id: "EntityId", relation_type: str | None = None, limit: int = 50
    ) -> list[EntityRelation]:
        q = select(orm.EntityRelation).where(orm.EntityRelation.from_entity_id == entity_id.value)
        if relation_type:
            q = q.where(orm.EntityRelation.predicate == relation_type)
        q = q.limit(limit)
        result = await self._session.execute(q)
        return [EntityRelationMapper.to_domain(r) for r in result.scalars()]
