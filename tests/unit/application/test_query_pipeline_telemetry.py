import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.ports.telemetry.telemetry_port import TelemetryPort, TelemetrySpan
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult


class FakeTelemetry(TelemetryPort):
    def __init__(self):
        self.events = []
        self._span_counter = 0

    async def start_span(self, name, kind, inputs, metadata=None, parent=None):
        self._span_counter += 1
        span = TelemetrySpan(
            span_id=f"span-{self._span_counter}",
            name=name,
            kind=kind,
            metadata=metadata or {},
        )
        self.events.append({"action": "start", "span": span, "inputs": inputs, "parent": parent})
        return span

    async def end_span(self, span, outputs=None, error=None):
        self.events.append({"action": "end", "span": span, "outputs": outputs, "error": error})

    async def add_metadata(self, span, metadata):
        self.events.append({"action": "metadata", "span": span, "metadata": metadata})


@pytest.fixture
def fake_telemetry():
    return FakeTelemetry()


@pytest.fixture
def mock_embedder():
    mock = AsyncMock()
    mock.embed.return_value = Embedding(vector=[0.1] * 1024)
    mock.set_parent_span = MagicMock()
    return mock


@pytest.fixture
def mock_vector_search():
    mock = AsyncMock()
    mock.search_similar.return_value = [
        SearchResult(content_id="1", content_type="section", title="Test", content="test", score=0.9)
    ]
    mock.set_parent_span = MagicMock()
    return mock


@pytest.fixture
def mock_keyword_search():
    mock = AsyncMock()
    mock.search_keyword.return_value = []
    mock.set_parent_span = MagicMock()
    return mock


@pytest.fixture
def mock_llm():
    mock = AsyncMock()
    mock.chat_completion.return_value = "This is a test answer."
    mock.last_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    mock.set_parent_span = MagicMock()
    return mock


@pytest.fixture
def mock_cache():
    mock = AsyncMock()
    mock.get.return_value = None
    mock.set_parent_span = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_execute_emits_root_span_and_step_spans(
    fake_telemetry, mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        telemetry=fake_telemetry,
    )
    result = await pipeline.execute(QueryInput(question="What is RAG?"))

    assert result["answer"] == "This is a test answer."
    start_events = [e for e in fake_telemetry.events if e["action"] == "start"]
    end_events = [e for e in fake_telemetry.events if e["action"] == "end"]

    span_names = [e["span"].name for e in start_events]
    # Only root span from pipeline._timed helper; step spans come from wrappers.
    assert "rag_query" in span_names
    assert len(start_events) == 1  # root only — wrappers are mocks

    # Root span is ended with outputs.
    root_end = [e for e in end_events if e["span"].name == "rag_query"]
    assert root_end
    assert root_end[0]["outputs"]["answer_length"] > 0


@pytest.mark.asyncio
async def test_execute_cache_hit_short_circuits(
    fake_telemetry, mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    mock_cache.get.return_value = json.dumps({
        "answer": "Cached answer",
        "sources": [],
        "tokens_used": 0,
        "cache_hit": False,
        "pipeline_steps": {},
    })
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        telemetry=fake_telemetry,
    )
    result = await pipeline.execute(QueryInput(question="Cached question?"))
    assert result["cache_hit"] is True

    metadata_events = [e for e in fake_telemetry.events if e["action"] == "metadata"]
    root_metadata = [e for e in metadata_events if e["span"].name == "rag_query"]
    assert root_metadata
    assert root_metadata[-1]["metadata"]["cache_hit"] is True


@pytest.mark.asyncio
async def test_execute_records_tokens_used(
    fake_telemetry, mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        telemetry=fake_telemetry,
    )
    result = await pipeline.execute(QueryInput(question="What is RAG?"))
    assert result["tokens_used"] == 120


@pytest.mark.asyncio
async def test_execute_error_records_error(
    fake_telemetry, mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    mock_embedder.embed.side_effect = RuntimeError("embed failed")
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
        telemetry=fake_telemetry,
    )
    with pytest.raises(RuntimeError):
        await pipeline.execute(QueryInput(question="What is RAG?"))

    # The pipeline now catches errors and records them directly on root_span.
    error_events = [e for e in fake_telemetry.events if e.get("error")]
    assert error_events
    # Verify the error was recorded against the root span.
    assert error_events[0]["span"].name == "rag_query"
