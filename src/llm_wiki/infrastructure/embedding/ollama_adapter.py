import logging
import httpx

from llm_wiki.application.ports.search.vector_search import EmbeddingServicePort
from llm_wiki.domain.value_objects.embedding import Embedding
from llm_wiki.config import settings

logger = logging.getLogger(__name__)


class OllamaEmbeddingAdapter(EmbeddingServicePort):
    def __init__(self, host: str = ""):
        self._host = (host or settings.ollama_host).rstrip("/")

    async def embed(self, text: str) -> Embedding:
        url = f"{self._host}/api/embeddings"
        payload = {"model": "bge-m3", "prompt": text}
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            return Embedding(vector=data["embedding"])

    async def embed_batch(self, texts: list[str]) -> list[Embedding]:
        embeddings = []
        for text in texts:
            emb = await self.embed(text)
            embeddings.append(emb)
        return embeddings
