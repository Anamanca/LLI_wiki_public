from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class SourceId:
    value: UUID


@dataclass(frozen=True)
class SourceItemId:
    value: UUID


@dataclass(frozen=True)
class PageId:
    value: UUID


@dataclass(frozen=True)
class EventId:
    value: UUID
