from abc import ABC, abstractmethod
from typing import Optional

from llm_wiki.domain.entities.source import Source, SourceItem
from llm_wiki.domain.value_objects.identifiers import SourceId, SourceItemId


class SourceRepository(ABC):
    @abstractmethod
    async def get_by_id(self, source_id: SourceId) -> Optional[Source]: ...

    @abstractmethod
    async def get_by_platform_external_id(self, platform: str, external_id: str) -> Optional[Source]: ...

    @abstractmethod
    async def list_active(self) -> list[Source]: ...

    @abstractmethod
    async def save(self, source: Source) -> Source: ...

    @abstractmethod
    async def delete(self, source_id: SourceId) -> None: ...


class SourceItemRepository(ABC):
    @abstractmethod
    async def get_by_id(self, item_id: SourceItemId) -> Optional[SourceItem]: ...

    @abstractmethod
    async def get_by_source_and_external_id(self, source_id: SourceId, external_id: str) -> Optional[SourceItem]: ...

    @abstractmethod
    async def save(self, item: SourceItem) -> SourceItem: ...

    @abstractmethod
    async def claim_next_pending(self) -> Optional[SourceItem]: ...

    @abstractmethod
    async def list_by_source(self, source_id: SourceId, limit: int = 50, offset: int = 0) -> list[SourceItem]: ...
