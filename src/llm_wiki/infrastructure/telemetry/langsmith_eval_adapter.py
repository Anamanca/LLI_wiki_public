"""LangSmith-backed evaluation adapter for the RAG pipeline."""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

from llm_wiki.config import settings

logger = logging.getLogger(__name__)

Evaluator = Callable[[dict[str, Any], dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]]


class LangSmithEvalAdapter:
    """Create datasets and run LLM-as-judge evaluations against LangSmith.

    This adapter intentionally avoids a LangChain dependency. Evaluators are
    plain async functions that receive a prediction run and a reference example
    and return a score dict with ``key``, ``score`` and ``comment``.
    """

    def __init__(
        self,
        api_key: str | None = None,
        api_url: str | None = None,
        project_name: str | None = None,
    ):
        try:
            from langsmith import Client
            from langsmith.evaluation import evaluate
        except ImportError as exc:  # pragma: no cover - env guard
            raise ImportError("langsmith is required for LangSmithEvalAdapter") from exc

        self._evaluate = evaluate
        self._client = Client(
            api_key=api_key or settings.langsmith_api_key,
            api_url=api_url or settings.langsmith_endpoint,
        )
        self._project_name = project_name or settings.langsmith_project

    def create_dataset(
        self,
        name: str,
        description: str,
        examples: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create or update a dataset with the given examples.

        Each example should have ``inputs`` and ``outputs`` keys:
        ``{"inputs": {"question": ...}, "outputs": {"expected_answer": ...}}``.
        """
        try:
            dataset = self._client.create_dataset(
                dataset_name=name,
                description=description,
            )
            dataset_id = dataset.id
        except Exception as exc:
            logger.warning("Failed to create dataset %s: %s", name, exc)
            raise

        created = []
        for example in examples:
            try:
                created.append(
                    self._client.create_example(
                        inputs=example.get("inputs", {}),
                        outputs=example.get("outputs", {}),
                        dataset_id=dataset_id,
                    )
                )
            except Exception as exc:
                logger.warning("Failed to create example: %s", exc)

        return {
            "dataset_id": str(dataset_id),
            "dataset_name": name,
            "examples_created": len(created),
        }

    def run_evaluation(
        self,
        dataset_name: str,
        target_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, dict[str, Any]]],
        evaluators: list[Evaluator] | None = None,
        experiment_name: str | None = None,
        max_concurrency: int = 2,
    ) -> dict[str, Any]:
        """Run a target function against a dataset and evaluate the results.

        The target function receives the example inputs and must return a dict
        with an ``answer`` key (and optionally ``sources``).
        """
        evaluators = evaluators or []

        async def _target(inputs: dict[str, Any]) -> dict[str, Any]:
            return await target_fn(inputs)

        def _wrap_evaluator(evaluator: Evaluator):
            def _run(run, example):
                return evaluator(run, example)
            return _run

        try:
            results = self._evaluate(
                _target,
                data=dataset_name,
                evaluators=[_wrap_evaluator(e) for e in evaluators],
                experiment_prefix=experiment_name,
                max_concurrency=max_concurrency,
            )
        except Exception as exc:
            logger.warning("Evaluation failed for dataset %s: %s", dataset_name, exc)
            raise

        return self._compute_metrics(results)

    def _compute_metrics(self, results: Any) -> dict[str, Any]:
        """Aggregate evaluation scores into a simple metrics dict."""
        scores: dict[str, list[float]] = {}
        latencies: list[float] = []
        total = 0

        for result in results:
            total += 1
            run = result.run
            if run and getattr(run, "extra", None):
                latency = run.extra.get("metadata", {}).get("total_latency_ms")
                if latency:
                    latencies.append(latency)

            for eval_result in result.evaluation_results or []:
                key = getattr(eval_result, "key", "unknown")
                score = getattr(eval_result, "score", None)
                if score is not None:
                    scores.setdefault(key, []).append(score)

        metrics: dict[str, Any] = {"total_examples": total}
        for key, values in scores.items():
            if values:
                metrics[f"{key}_mean"] = round(sum(values) / len(values), 4)
                metrics[f"{key}_min"] = round(min(values), 4)
                metrics[f"{key}_max"] = round(max(values), 4)

        if latencies:
            latencies.sort()
            metrics["latency_p50_ms"] = round(latencies[len(latencies) // 2], 2)
            metrics["latency_p95_ms"] = round(latencies[int(len(latencies) * 0.95)], 2)

        return metrics


async def correctness_evaluator(run: dict[str, Any], example: dict[str, Any]) -> dict[str, Any]:
    """Stub evaluator: compare prediction to expected answer using a simple overlap.

    This is a placeholder. A real LLM-as-judge evaluator would call an LLM with a
    grading prompt. The stub is useful for unit tests and dry-runs.
    """
    prediction = (run.get("outputs") or {}).get("answer", "")
    expected = (example.get("outputs") or {}).get("expected_answer", "")
    if not expected:
        return {"key": "correctness", "score": None, "comment": "No reference answer"}

    pred_set = set(prediction.lower().split())
    exp_set = set(expected.lower().split())
    score = 0.0 if not pred_set else round(len(pred_set & exp_set) / len(pred_set), 4)
    return {"key": "correctness", "score": score, "comment": f"Overlap: {score}"}


async def relevance_evaluator(run: dict[str, Any], example: dict[str, Any]) -> dict[str, Any]:
    """Stub relevance evaluator: check if question keywords appear in the answer."""
    inputs = example.get("inputs") or {}
    question = inputs.get("question", "")
    prediction = (run.get("outputs") or {}).get("answer", "")
    keywords = set(question.lower().split())
    pred_set = set(prediction.lower().split())
    score = 0.0 if not keywords else round(len(keywords & pred_set) / len(keywords), 4)
    return {"key": "relevance", "score": score, "comment": f"Keyword overlap: {score}"}


async def faithfulness_evaluator(run: dict[str, Any], example: dict[str, Any]) -> dict[str, Any]:
    """Stub faithfulness evaluator: check if answer cites sources when sources exist."""
    outputs = run.get("outputs") or {}
    answer = outputs.get("answer", "")
    sources = outputs.get("sources", [])
    has_citations = "[" in answer and "]" in answer
    score = 1.0 if (sources and has_citations) or not sources else 0.5
    return {"key": "faithfulness", "score": score, "comment": f"Has citations: {has_citations}"}
