from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Embedding:
    vector: list[float]
    dimensions: int = 1024

    def __post_init__(self):
        if len(self.vector) != self.dimensions:
            raise ValueError(f"Expected {self.dimensions} dimensions, got {len(self.vector)}")


@dataclass(frozen=True)
class SearchResult:
    content_id: str
    content_type: str
    title: str
    content: str
    score: float
    metadata: Optional[dict] = None
