from abc import ABC, abstractmethod
from datetime import date

from llm_wiki.domain.entities.event import EventCanonical, EventObservation, EventTimelineChain
from llm_wiki.domain.value_objects.identifiers import EventId, PageId


class EventRepository(ABC):
    @abstractmethod
    async def get_by_id(self, event_id: EventId) -> EventCanonical | None: ...

    @abstractmethod
    async def save(self, event: EventCanonical) -> EventCanonical: ...

    @abstractmethod
    async def save_observation(self, observation: EventObservation) -> EventObservation: ...

    @abstractmethod
    async def save_timeline_chain(self, chain: EventTimelineChain) -> EventTimelineChain: ...

    @abstractmethod
    async def find_by_title(self, title: str, limit: int = 5) -> list[EventCanonical]: ...

    @abstractmethod
    async def list_observations_for_event(self, event_id: EventId) -> list[EventObservation]: ...

    @abstractmethod
    async def list_by_date_range(
        self, start_date: date, end_date: date | None = None, limit: int = 50
    ) -> list[EventCanonical]: ...

    @abstractmethod
    async def search_vector(
        self, embedding: list[float], top_k: int = 10
    ) -> list[EventCanonical]: ...

    @abstractmethod
    async def get_timeline_chains(
        self, event_id: EventId, direction: str = "both"
    ) -> list[EventTimelineChain]: ...

    @abstractmethod
    async def list_observations_by_page(self, page_id: PageId) -> list[EventObservation]: ...
