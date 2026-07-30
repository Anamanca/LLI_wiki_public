from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime


@dataclass
class ChatMessage:
    role: str
    content: str


@dataclass
class ChatSession:
    id: str
    title: str
    messages: list[ChatMessage]
    created_at: datetime
    updated_at: datetime


class ChatSessionRepository(ABC):
    @abstractmethod
    async def list_sessions(self) -> list[ChatSession]: ...

    @abstractmethod
    async def get_by_id(self, session_id: str) -> ChatSession | None: ...

    @abstractmethod
    async def create(self, title: str | None = None) -> ChatSession: ...

    @abstractmethod
    async def save(self, session: ChatSession) -> ChatSession: ...

    @abstractmethod
    async def delete(self, session_id: str) -> bool: ...
