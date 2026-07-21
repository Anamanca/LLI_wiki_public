import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.dto.query_dto import QueryInput
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult


@pytest.fixture
def mock_embedder():
    mock = AsyncMock()
    mock.embed.return_value = Embedding(vector=[0.1] * 1024)
    return mock


@pytest.fixture
def mock_vector_search():
    mock = AsyncMock()
    mock.search_similar.return_value = [
        SearchResult(
            content_id="1", content_type="section", title="Test",
            content="test content", score=0.9
        )
    ]
    return mock


@pytest.fixture
def mock_keyword_search():
    mock = AsyncMock()
    mock.search_keyword.return_value = []
    return mock


@pytest.fixture
def mock_llm():
    mock = AsyncMock()
    mock.chat_completion_reasoning.return_value = {
        "content": "This is a test answer.",
        "reasoning_content": "",
    }
    return mock


@pytest.fixture
def mock_cache():
    mock = AsyncMock()
    mock.get.return_value = None
    mock.semantic_get.return_value = None
    mock.semantic_set.return_value = None
    return mock


@pytest.mark.asyncio
async def test_query_pipeline_returns_answer(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    pipeline = QueryPipeline(
        embedder=mock_embedder,
        vector_search=mock_vector_search,
        keyword_search=mock_keyword_search,
        llm=mock_llm,
        cache=mock_cache,
    )

    result = await pipeline.execute(QueryInput(question="What is RAG?"))

    assert "answer" in result
    assert result["answer"] == "This is a test answer."
    assert "sources" in result
    assert result["cache_hit"] is False


@pytest.mark.asyncio
async def test_query_pipeline_uses_cache(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    import json
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
    )

    result = await pipeline.execute(QueryInput(question="Cached question?"))

    assert result["cache_hit"] is True
    assert result["answer"] == "Cached answer"
    # Exact cache hit should skip embedding
    mock_embedder.embed.assert_not_called()


@pytest.mark.asyncio
async def test_semantic_cache_hit_skips_retrieval_and_llm(
    mock_embedder, mock_vector_search, mock_keyword_search, mock_llm, mock_cache
):
    """P2: semantic cache hit on exact miss → skip expensive pipeline."""
    import json

    # Exact cache miss
    mock_cache.get.return_value = None
    # Semantic cache hit
    mock_cache.semantic_get.return_value = json.dumps({
        "answer": "Semantic cached answer",
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
    )

    result = await pipeline.execute(QueryInput(question="What is RAG?"))

    assert result["cache_hit"] is True
    assert result["answer"] == "Semantic cached answer"
    # Embedding was needed for semantic check, but no search / LLM
    mock_embedder.embed.assert_called_once()
    mock_vector_search.search_similar.assert_not_called()
    mock_llm.chat_completion_reasoning.assert_not_called()


