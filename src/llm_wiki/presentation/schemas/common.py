from pydantic import BaseModel
from typing import Optional


class QueryRequest(BaseModel):
    question: str
    source_id: Optional[str] = None
    top_k: Optional[int] = 10
    stream: bool = False


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
    config: Optional[dict] = None


class SourceResponse(BaseModel):
    id: str
    name: str
    platform: str
    external_id: str
    url: str
    status: str
    config: dict
    added_at: Optional[str] = None
    last_checked_at: Optional[str] = None
