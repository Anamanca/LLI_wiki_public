import pytest
from unittest.mock import MagicMock, patch

from llm_wiki.infrastructure.telemetry.langsmith_eval_adapter import (
    LangSmithEvalAdapter,
    correctness_evaluator,
    faithfulness_evaluator,
    relevance_evaluator,
)


class TestLangSmithEvalAdapter:
    @pytest.fixture
    def adapter(self):
        with patch("langsmith.Client") as MockClient, patch(
            "langsmith.evaluation.evaluate"
        ) as mock_evaluate:
            adapter = LangSmithEvalAdapter(api_key="test-key")
            adapter._client = MockClient()
            adapter._evaluate = mock_evaluate
            yield adapter

    def test_create_dataset(self, adapter):
        mock_dataset = MagicMock()
        mock_dataset.id = "dataset-id"
        adapter._client.create_dataset.return_value = mock_dataset
        result = adapter.create_dataset(
            name="test-dataset",
            description="test",
            examples=[
                {"inputs": {"question": "q1"}, "outputs": {"expected_answer": "a1"}}
            ],
        )
        assert result["dataset_id"] == "dataset-id"
        assert result["examples_created"] == 1

    def test_run_evaluation(self, adapter):
        mock_result = MagicMock()
        mock_result.run.extra = {"metadata": {"total_latency_ms": 123.0}}
        mock_result.evaluation_results = []
        adapter._evaluate.return_value = [mock_result]
        metrics = adapter.run_evaluation(
            dataset_name="test-dataset",
            target_fn=MagicMock(),
            evaluators=[],
        )
        assert metrics["total_examples"] == 1
        assert metrics["latency_p50_ms"] == 123.0


class TestStubEvaluators:
    @pytest.mark.asyncio
    async def test_correctness_evaluator(self):
        run = {"outputs": {"answer": "the quick brown fox"}}
        example = {"outputs": {"expected_answer": "the quick brown fox jumps"}}
        result = await correctness_evaluator(run, example)
        assert result["key"] == "correctness"
        assert 0 <= result["score"] <= 1

    @pytest.mark.asyncio
    async def test_relevance_evaluator(self):
        run = {"outputs": {"answer": "hello world"}}
        example = {"inputs": {"question": "hello"}}
        result = await relevance_evaluator(run, example)
        assert result["key"] == "relevance"
        assert result["score"] == 1.0

    @pytest.mark.asyncio
    async def test_faithfulness_evaluator(self):
        run = {"outputs": {"answer": "see [1]", "sources": [{"id": "1"}]}}
        example = {}
        result = await faithfulness_evaluator(run, example)
        assert result["key"] == "faithfulness"
        assert result["score"] == 1.0
