from unittest.mock import AsyncMock

import pytest

from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.infrastructure.embedding.traced_embedding_wrapper import TracedEmbeddingWrapper
from llm_wiki.infrastructure.llm.traced_llm_wrapper import TracedLLMWrapper
from llm_wiki.infrastructure.persistence.redis.traced_cache_wrapper import TracedCacheWrapper
from llm_wiki.infrastructure.search.traced_search_wrapper import (
    TracedKeywordSearchWrapper,
    TracedVectorSearchWrapper,
)


class FakeTelemetry(TelemetryPort):
    def __init__(self):
        self.events = []

    async def start_span(self, name, kind, inputs, metadata=None, parent=None):
        span = TelemetrySpan(
            span_id=f"span-{len(self.events)}", name=name, kind=kind, metadata=metadata or {}
        )
        self.events.append({"action": "start", "span": span, "inputs": inputs})
        return span

    async def end_span(self, span, outputs=None, error=None, metadata=None):
        self.events.append({"action": "end", "span": span, "outputs": outputs, "error": error, "metadata": metadata})

    async def add_metadata(self, span, metadata):
        self.events.append({"action": "metadata", "span": span, "metadata": metadata})


@pytest.fixture
def fake_telemetry():
    return FakeTelemetry()


class TestTracedLLMWrapper:
    @pytest.mark.asyncio
    async def test_chat_completion_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.chat_completion.return_value = "hello"
        inner.last_usage = {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}
        wrapper = TracedLLMWrapper(inner, fake_telemetry, model="gpt-4")
        answer = await wrapper.chat_completion([{"role": "user", "content": "hi"}])
        assert answer == "hello"
        starts = [e for e in fake_telemetry.events if e["action"] == "start"]
        ends = [e for e in fake_telemetry.events if e["action"] == "end"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0]["span"].name == "llm_chat_completion"

    @pytest.mark.asyncio
    async def test_chat_completion_records_error(self, fake_telemetry):
        inner = AsyncMock()
        inner.chat_completion.side_effect = RuntimeError("boom")
        wrapper = TracedLLMWrapper(inner, fake_telemetry, model="gpt-4")
        with pytest.raises(RuntimeError):
            await wrapper.chat_completion([])
        errors = [e for e in fake_telemetry.events if e.get("error")]
        assert len(errors) == 1

    @pytest.mark.asyncio
    async def test_chat_completion_raw_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.chat_completion_raw.return_value = {
            "choices": [{"message": {"content": "raw hello"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        wrapper = TracedLLMWrapper(inner, fake_telemetry, model="gpt-4")
        result = await wrapper.chat_completion_raw([{"role": "user", "content": "hi"}])
        assert result["choices"][0]["message"]["content"] == "raw hello"
        starts = [e for e in fake_telemetry.events if e["action"] == "start"]
        ends = [e for e in fake_telemetry.events if e["action"] == "end"]
        assert len(starts) == 1
        assert len(ends) == 1
        assert starts[0]["span"].name == "llm_chat_completion_raw"
        ends = [e for e in fake_telemetry.events if e["action"] == "end"]
        assert ends[0]["metadata"]["tokens_used"] == 15


class TestTracedVectorSearchWrapper:
    @pytest.mark.asyncio
    async def test_search_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.search_similar.return_value = [
            SearchResult(content_id="1", content_type="section", title="T", content="c", score=0.9)
        ]
        wrapper = TracedVectorSearchWrapper(inner, fake_telemetry)
        results = await wrapper.search_similar(Embedding(vector=[0.1] * 1024), top_k=5)
        assert len(results) == 1
        starts = [e for e in fake_telemetry.events if e["action"] == "start"]
        assert starts[0]["span"].name == "vector_search"


class TestTracedKeywordSearchWrapper:
    @pytest.mark.asyncio
    async def test_search_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.search_keyword.return_value = []
        wrapper = TracedKeywordSearchWrapper(inner, fake_telemetry)
        results = await wrapper.search_keyword("query", top_k=5)
        assert results == []
        starts = [e for e in fake_telemetry.events if e["action"] == "start"]
        assert starts[0]["span"].name == "keyword_search"


class TestTracedEmbeddingWrapper:
    @pytest.mark.asyncio
    async def test_embed_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.embed.return_value = Embedding(vector=[0.1] * 1024)
        wrapper = TracedEmbeddingWrapper(inner, fake_telemetry, model="bge-m3")
        result = await wrapper.embed("hello")
        assert result is not None
        starts = [e for e in fake_telemetry.events if e["action"] == "start"]
        assert starts[0]["span"].name == "embedding"


class TestTracedCacheWrapper:
    @pytest.mark.asyncio
    async def test_get_hit_emits_span(self, fake_telemetry):
        inner = AsyncMock()
        inner.get.return_value = "cached"
        wrapper = TracedCacheWrapper(inner, fake_telemetry)
        value = await wrapper.get("key")
        assert value == "cached"
        ends = [e for e in fake_telemetry.events if e["action"] == "end"]
        assert ends[0]["outputs"]["cache_hit"] is True
