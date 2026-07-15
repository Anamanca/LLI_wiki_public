from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.repositories.page_repository import PageRepository, PageSectionRepository
from llm_wiki.domain.entities.page import Page, PageSection
from llm_wiki.domain.value_objects.identifiers import PageId, SourceId
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.persistence.postgres.mappers import PageMapper, PageSectionMapper


class PostgresPageRepository(PageRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, page_id: PageId) -> Optional[Page]:
        result = await self._session.execute(select(orm.Page).where(orm.Page.id == page_id.value))
        row = result.scalar_one_or_none()
        return PageMapper.to_domain(row) if row else None

    async def get_by_slug(self, slug: str) -> Optional[Page]:
        result = await self._session.execute(select(orm.Page).where(orm.Page.slug == slug))
        row = result.scalar_one_or_none()
        return PageMapper.to_domain(row) if row else None

    async def save(self, page: Page) -> Page:
        existing = await self._session.get(orm.Page, page.id.value)
        orm_page = PageMapper.to_orm(page, existing)
        self._session.add(orm_page)
        await self._session.flush()
        return PageMapper.to_domain(orm_page)

    async def delete(self, page_id: PageId) -> None:
        page = await self._session.get(orm.Page, page_id.value)
        if page:
            await self._session.delete(page)
            await self._session.flush()

    async def list_by_source(self, source_id: SourceId) -> list[Page]:
        result = await self._session.execute(
            select(orm.Page).where(orm.Page.source_id == source_id.value)
        )
        return [PageMapper.to_domain(r) for r in result.scalars()]

    async def search_by_title(self, query: str, limit: int = 10) -> list[Page]:
        result = await self._session.execute(
            select(orm.Page)
            .where(orm.Page.title.ilike(f"%{query}%"))
            .limit(limit)
        )
        return [PageMapper.to_domain(r) for r in result.scalars()]

    async def list_all(self, limit: int = 50, offset: int = 0) -> list[Page]:
        result = await self._session.execute(
            select(orm.Page)
            .order_by(orm.Page.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        return [PageMapper.to_domain(r) for r in result.scalars()]


class PostgresPageSectionRepository(PageSectionRepository):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def get_by_id(self, section_id: PageId) -> Optional[PageSection]:
        result = await self._session.execute(
            select(orm.PageSection).where(orm.PageSection.id == section_id.value)
        )
        row = result.scalar_one_or_none()
        return PageSectionMapper.to_domain(row) if row else None

    async def list_by_page(self, page_id: PageId) -> list[PageSection]:
        result = await self._session.execute(
            select(orm.PageSection)
            .where(orm.PageSection.page_id == page_id.value)
            .order_by(orm.PageSection.section_order)
        )
        return [PageSectionMapper.to_domain(r) for r in result.scalars()]

    async def save(self, section: PageSection) -> PageSection:
        existing = await self._session.get(orm.PageSection, section.id.value)
        orm_section = PageSectionMapper.to_orm(section, existing)
        self._session.add(orm_section)
        await self._session.flush()
        return PageSectionMapper.to_domain(orm_section)

    async def delete_by_page(self, page_id: PageId) -> None:
        await self._session.execute(
            orm.PageSection.__table__.delete().where(orm.PageSection.page_id == page_id.value)
        )
        await self._session.flush()
