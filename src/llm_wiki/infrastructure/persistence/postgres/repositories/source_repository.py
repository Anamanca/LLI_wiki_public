from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.source_repository import SourceRepository, SourceItemRepository
from llm_wiki.domain.entities.source import Source, SourceItem
from llm_wiki.domain.value_objects.identifiers import SourceId, SourceItemId
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.persistence.postgres.mappers import SourceMapper, SourceItemMapper


class PostgresSourceRepository(SourceRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, source_id: SourceId) -> Optional[Source]:
        result = await self._session.execute(
            select(orm.Source).where(orm.Source.id == source_id.value)
        )
        row = result.scalar_one_or_none()
        return SourceMapper.to_domain(row) if row else None

    async def get_by_platform_external_id(self, platform: str, external_id: str) -> Optional[Source]:
        result = await self._session.execute(
            select(orm.Source).where(
                orm.Source.platform == platform,
                orm.Source.external_id == external_id,
            )
        )
        row = result.scalar_one_or_none()
        return SourceMapper.to_domain(row) if row else None

    async def list_active(self) -> list[Source]:
        result = await self._session.execute(
            select(orm.Source).where(orm.Source.status == "active")
        )
        return [SourceMapper.to_domain(r) for r in result.scalars()]

    async def save(self, source: Source) -> Source:
        existing = await self._session.get(orm.Source, source.id.value)
        orm_source = SourceMapper.to_orm(source, existing)
        self._session.add(orm_source)
        await self._session.flush()
        return SourceMapper.to_domain(orm_source)

    async def delete(self, source_id: SourceId) -> None:
        source = await self._session.get(orm.Source, source_id.value)
        if source:
            await self._session.delete(source)
            await self._session.flush()


class PostgresSourceItemRepository(SourceItemRepository):
    def __init__(self, session: AsyncSession):
        self._session = session
        self._mapper = SourceItemMapper()

    async def get_by_id(self, item_id: SourceItemId) -> Optional[SourceItem]:
        result = await self._session.execute(
            select(orm.SourceItem).where(orm.SourceItem.id == item_id.value)
        )
        row = result.scalar_one_or_none()
        return self._mapper.to_domain(row) if row else None

    async def get_by_source_and_external_id(
        self, source_id: SourceId, external_id: str
    ) -> Optional[SourceItem]:
        result = await self._session.execute(
            select(orm.SourceItem).where(
                orm.SourceItem.source_id == source_id.value,
                orm.SourceItem.external_id == external_id,
            )
        )
        row = result.scalar_one_or_none()
        return self._mapper.to_domain(row) if row else None

    async def save(self, item: SourceItem) -> SourceItem:
        existing = await self._session.get(orm.SourceItem, item.id.value)
        orm_item = self._mapper.to_orm(item, existing)
        self._session.add(orm_item)
        await self._session.flush()
        return self._mapper.to_domain(orm_item)

    async def claim_next_pending(self) -> Optional[SourceItem]:
        result = await self._session.execute(
            select(orm.SourceItem)
            .where(orm.SourceItem.status == "pending")
            .where(
                or_(
                    orm.SourceItem.retry_after.is_(None),
                    orm.SourceItem.retry_after <= datetime.now(tz=timezone.utc),
                )
            )
            .order_by(orm.SourceItem.priority.desc(), orm.SourceItem.created_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        orm_item = result.scalar_one_or_none()
        if not orm_item:
            return None
        orm_item.status = "processing"
        orm_item.started_at = datetime.now(tz=timezone.utc)
        orm_item.error_message = None
        await self._session.commit()
        return self._mapper.to_domain(orm_item)

    async def list_by_source(
        self, source_id: SourceId, limit: int = 50, offset: int = 0
    ) -> list[SourceItem]:
        result = await self._session.execute(
            select(orm.SourceItem)
            .where(orm.SourceItem.source_id == source_id.value)
            .order_by(orm.SourceItem.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [self._mapper.to_domain(r) for r in result.scalars()]
