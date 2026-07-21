from abc import ABC, abstractmethod
from typing import Optional

from llm_wiki.domain.entities.page import Page, PageSection
from llm_wiki.domain.value_objects.identifiers import PageId, SourceId


class PageRepository(ABC):
    @abstractmethod
    async def get_by_id(self, page_id: PageId) -> Optional[Page]: ...

    @abstractmethod
    async def get_by_slug(self, slug: str) -> Optional[Page]: ...

    @abstractmethod
    async def save(self, page: Page) -> Page: ...

    @abstractmethod
    async def delete(self, page_id: PageId) -> None: ...

    @abstractmethod
    async def list_by_source(self, source_id: SourceId) -> list[Page]: ...

    @abstractmethod
    async def list_all(
        self,
        limit: int = 50,
        offset: int = 0,
        search: str | None = None,
        sort_by: str = "updated_at",
        sort_order: str = "desc",
    ) -> tuple[list[Page], int]: ...

    @abstractmethod
    async def search_by_title(self, query: str, limit: int = 10) -> list[Page]: ...


class PageSectionRepository(ABC):
    @abstractmethod
    async def get_by_id(self, section_id: "PageSectionId") -> Optional[PageSection]: ...

    @abstractmethod
    async def list_by_page(self, page_id: PageId) -> list[PageSection]: ...

    @abstractmethod
    async def save(self, section: PageSection) -> PageSection: ...

    @abstractmethod
    async def delete_by_page(self, page_id: PageId) -> None: ...
