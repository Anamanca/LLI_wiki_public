import json
import pytest
from unittest.mock import AsyncMock, MagicMock

from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult


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
        SearchResult(
            content_id="1", content_type="section", title="Test",
            content="test content", score=0.9,
            metadata={"page_title": "Page", "page_slug": "page", "source_name": "src"},
        )
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
    mock.chat_completion_reasoning.return_value = {
        "content": "This is a detailed test answer.",
        "reasoning_content": "reasoning...",
    }
    mock.last_usage = {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120}
    mock.set_parent_span = MagicMock()
    return mock


@pytest.fixture
def mock_cache():
    mock = AsyncMock()
    mock.get.return_value = None
    mock.set.return_value = None
    mock.semantic_get.return_value = None
    mock.semantic_set.return_value = None
    mock.set_parent_span = MagicMock()
    return mock


@pytest.mark.asyncio
async def test_execute_stream_yields_status_sequence_and_complete(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    events = []
    async for event in pipeline.execute_stream(QueryInput(question="What is RAG?")):
        events.append(event)

    status_events = [e for e in events if e["type"] == "status"]
    complete_events = [e for e in events if e["type"] == "complete"]

    assert [e["data"]["status"] for e in status_events] == [
        "processing", "retrieving", "thinking", "summarizing"
    ]
    assert len(complete_events) == 1
    complete = complete_events[0]["data"]
    assert complete["answer"] == "This is a detailed test answer."
    assert len(complete["citations"]) == 1
    assert complete["tokens_used"] == 120


@pytest.mark.asyncio
async def test_execute_stream_chat_history_passed_to_llm(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    async for _ in pipeline.execute_stream(QueryInput(
        question="Follow-up?",
        chat_history=[
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ],
    )):
        pass

    call = mock_llm.chat_completion_reasoning.await_args
    messages = call.kwargs["messages"]
    roles = [m["role"] for m in messages]
    assert "system" in roles
    assert roles.count("user") >= 2
    assert "assistant" in roles


@pytest.mark.asyncio
async def test_execute_stream_propagates_llm_error(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    mock_llm.chat_completion_reasoning.side_effect = RuntimeError("LLM failed")
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    with pytest.raises(RuntimeError, match="LLM failed"):
        async for _ in pipeline.execute_stream(QueryInput(question="What is RAG?")):
            pass


@pytest.mark.asyncio
async def test_execute_stream_exact_cache_hit_skips_pipeline(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    """P0: stream endpoint returns cached answer immediately on exact hit."""
    import json

    mock_cache.get.return_value = json.dumps({
        "answer": "Cached stream answer",
        "sources": [],
        "tokens_used": 0,
    })

    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    events = []
    async for event in pipeline.execute_stream(QueryInput(question="Cached stream?")):
        events.append(event)

    # Should have exactly one complete event, no status/progress events
    complete_events = [e for e in events if e["type"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["answer"] == "Cached stream answer"
    # No embedding, search, or LLM calls
    mock_embedder.embed.assert_not_called()
    mock_vector_search.search_similar.assert_not_called()
    mock_llm.chat_completion_reasoning.assert_not_called()


@pytest.mark.asyncio
async def test_execute_stream_semantic_cache_hit(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    """P2: stream endpoint uses semantic cache after exact miss."""
    import json

    # Exact miss, semantic hit
    mock_cache.get.return_value = None
    mock_cache.semantic_get.return_value = json.dumps({
        "answer": "Semantic stream answer",
        "sources": [],
        "tokens_used": 0,
    })

    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    events = []
    async for event in pipeline.execute_stream(QueryInput(question="RAG là gì?")):
        events.append(event)

    complete_events = [e for e in events if e["type"] == "complete"]
    assert len(complete_events) == 1
    assert complete_events[0]["data"]["answer"] == "Semantic stream answer"
    # Embedding was needed, but not search/LLM
    mock_embedder.embed.assert_called_once()
    mock_vector_search.search_similar.assert_not_called()
    mock_llm.chat_completion_reasoning.assert_not_called()
