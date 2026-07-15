from datetime import date
from typing import Optional

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.domain.entities.event import EventCanonical, EventObservation, EventTimelineChain
from llm_wiki.domain.value_objects.identifiers import EventId, PageId
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.persistence.postgres.mappers import EventCanonicalMapper, EventObservationMapper, EventTimelineChainMapper


class PostgresEventRepository(EventRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, event_id: EventId) -> Optional[EventCanonical]:
        result = await self._session.execute(
            select(orm.EventCanonical).where(orm.EventCanonical.id == event_id.value)
        )
        row = result.scalar_one_or_none()
        return EventCanonicalMapper.to_domain(row) if row else None

    async def save(self, event: EventCanonical) -> EventCanonical:
        existing = await self._session.get(orm.EventCanonical, event.id.value)
        orm_event = EventCanonicalMapper.to_orm(event, existing)
        self._session.add(orm_event)
        await self._session.flush()
        return EventCanonicalMapper.to_domain(orm_event)

    async def save_observation(self, observation: EventObservation) -> EventObservation:
        existing = await self._session.get(orm.EventObservation, observation.id.value)
        orm_obs = EventObservationMapper.to_orm(observation, existing)
        self._session.add(orm_obs)
        await self._session.flush()
        return EventObservationMapper.to_domain(orm_obs)

    async def save_timeline_chain(self, chain: EventTimelineChain) -> EventTimelineChain:
        existing = await self._session.get(orm.EventTimelineChain, chain.id.value)
        orm_chain = EventTimelineChainMapper.to_orm(chain, existing)
        self._session.add(orm_chain)
        await self._session.flush()
        return EventTimelineChainMapper.to_domain(orm_chain)

    async def find_by_title(self, title: str, limit: int = 5) -> list[EventCanonical]:
        result = await self._session.execute(
            select(orm.EventCanonical)
            .where(orm.EventCanonical.title.ilike(f"%{title}%"))
            .limit(limit)
        )
        return [EventCanonicalMapper.to_domain(r) for r in result.scalars()]

    async def list_observations_for_event(self, event_id: EventId) -> list[EventObservation]:
        result = await self._session.execute(
            select(orm.EventObservation).where(orm.EventObservation.event_id == event_id.value)
        )
        return [EventObservationMapper.to_domain(r) for r in result.scalars()]

    async def list_by_date_range(
        self, start_date: date, end_date: Optional[date] = None, limit: int = 50
    ) -> list[EventCanonical]:
        q = select(orm.EventCanonical).where(
            orm.EventCanonical.normalized_date >= start_date
        )
        if end_date:
            q = q.where(orm.EventCanonical.normalized_date <= end_date)
        q = q.limit(limit)
        result = await self._session.execute(q)
        return [EventCanonicalMapper.to_domain(r) for r in result.scalars()]

    async def search_vector(
        self, embedding: list[float], top_k: int = 10
    ) -> list[EventCanonical]:
        result = await self._session.execute(
            select(orm.EventCanonical)
            .where(orm.EventCanonical.canonical_embedding.isnot(None))
            .order_by(orm.EventCanonical.canonical_embedding.cosine_distance(embedding))
            .limit(top_k)
        )
        return [EventCanonicalMapper.to_domain(r) for r in result.scalars()]

    async def get_timeline_chains(
        self, event_id: EventId, direction: str = "both"
    ) -> list[EventTimelineChain]:
        conditions = []
        if direction in ("from", "both"):
            conditions.append(orm.EventTimelineChain.from_event_id == event_id.value)
        if direction in ("to", "both"):
            conditions.append(orm.EventTimelineChain.to_event_id == event_id.value)
        result = await self._session.execute(
            select(orm.EventTimelineChain).where(
                or_(*conditions) if len(conditions) == 2 else conditions[0]
            )
        )
        return [EventTimelineChainMapper.to_domain(r) for r in result.scalars()]

    async def list_observations_by_page(self, page_id: PageId) -> list[EventObservation]:
        result = await self._session.execute(
            select(orm.EventObservation).where(orm.EventObservation.page_id == page_id.value)
        )
        return [EventObservationMapper.to_domain(r) for r in result.scalars()]


