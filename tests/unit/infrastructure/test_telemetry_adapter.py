from unittest.mock import MagicMock, patch

import pytest

from llm_wiki.infrastructure.telemetry.langsmith_telemetry_adapter import (
    LangSmithTelemetryAdapter,
)
from llm_wiki.infrastructure.telemetry.null_telemetry_adapter import NullTelemetryAdapter


class TestNullTelemetryAdapter:
    @pytest.mark.asyncio
    async def test_start_span_returns_handle(self):
        adapter = NullTelemetryAdapter()
        span = await adapter.start_span("test", "chain", {"x": 1})
        assert span.name == "test"
        assert span.kind == "chain"

    @pytest.mark.asyncio
    async def test_end_span_is_no_op(self):
        adapter = NullTelemetryAdapter()
        span = await adapter.start_span("test", "chain", {})
        await adapter.end_span(span, outputs={"x": 1})

    @pytest.mark.asyncio
    async def test_add_metadata_is_no_op(self):
        adapter = NullTelemetryAdapter()
        span = await adapter.start_span("test", "chain", {})
        await adapter.add_metadata(span, {"x": 1})


class TestLangSmithTelemetryAdapter:
    @pytest.fixture
    def mock_run_tree(self):
        run = MagicMock()
        run.id = "run-id"
        run.post = MagicMock()
        run.end = MagicMock()
        run.patch = MagicMock()
        run.extra = {}
        return run

    @pytest.fixture
    def adapter(self, mock_run_tree):
        with (
            patch("langsmith.Client") as MockClient,
            patch(
                "langsmith.run_trees.RunTree",
                return_value=mock_run_tree,
            ),
        ):
            adapter = LangSmithTelemetryAdapter(
                api_key="test-key",
                api_url="https://test.langsmith.com",
                project_name="test-project",
            )
            adapter._client = MockClient()
            yield adapter

    @pytest.mark.asyncio
    async def test_start_span_creates_run(self, adapter, mock_run_tree):
        span = await adapter.start_span("rag_query", "chain", {"q": "hello"})
        assert span.name == "rag_query"
        assert span.kind == "chain"
        mock_run_tree.post.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_span_ends_run(self, adapter, mock_run_tree):
        span = await adapter.start_span("rag_query", "chain", {})
        await adapter.end_span(span, outputs={"answer": "hi"})
        mock_run_tree.end.assert_called_once()
        mock_run_tree.patch.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_metadata_updates_extra(self, adapter, mock_run_tree):
        span = await adapter.start_span("rag_query", "chain", {})
        await adapter.add_metadata(span, {"latency_ms": 12.3})
        assert mock_run_tree.extra["metadata"]["latency_ms"] == 12.3

    @pytest.mark.asyncio
    async def test_start_span_degrades_on_exception(self, adapter, mock_run_tree):
        mock_run_tree.post.side_effect = RuntimeError("boom")
        span = await adapter.start_span("rag_query", "chain", {})
        assert span.name == "rag_query"
