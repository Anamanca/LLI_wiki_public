from dataclasses import dataclass
from datetime import datetime


@dataclass
class QueryInput:
    question: str
    source_id: str | None = None
    top_k: int = 10
    stream: bool = False
    chat_history: list[dict] | None = None
    from_date: datetime | None = None
    to_date: datetime | None = None


@dataclass
class QueryResult:
    answer: str
    sources: list[dict]
    tokens_used: int
    cache_hit: bool
    pipeline_steps: dict


class QueryResponse:
    def __init__(
        self,
        answer: str,
        sources: list[dict],
        pipeline_steps: dict,
        cache_hit: bool = False,
    ):
        self.answer = answer
        self.sources = sources
        self.pipeline_steps = pipeline_steps
        self.cache_hit = cache_hit

    def to_dict(self) -> dict:
        return {
            "answer": self.answer,
            "sources": self.sources,
            "pipeline_steps": self.pipeline_steps,
            "cache_hit": self.cache_hit,
        }
