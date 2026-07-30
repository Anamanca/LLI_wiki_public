from datetime import datetime

from pydantic import BaseModel


class ChatMessageItem(BaseModel):
    role: str
    content: str


class QueryRequest(BaseModel):
    question: str
    source_id: str | None = None
    top_k: int | None = 10
    stream: bool = False
    history: list[ChatMessageItem] | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


class QueryResponseModel(BaseModel):
    answer: str
    citations: list[dict]
    sources_used: list[dict]
    tokens_used: int
    latency_ms: float


class ErrorResponse(BaseModel):
    error: str
    detail: str


class PaginatedResponse(BaseModel):
    items: list
    total: int
    page: int
    page_size: int


class SourceCreateRequest(BaseModel):
    name: str
    platform: str = "youtube"
    external_id: str
    url: str
    config: dict | None = None


class SourceResponse(BaseModel):
    id: str
    name: str
    platform: str
    external_id: str
    url: str
    status: str
    config: dict
    added_at: str | None = None
    last_checked_at: str | None = None
    last_video_published_at: str | None = None
