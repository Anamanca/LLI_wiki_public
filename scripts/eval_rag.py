"""Evaluate the RAG pipeline against a dataset and optionally push to LangSmith.

Usage:
    # Dry-run with stub evaluators (no LangSmith API calls)
    python scripts/eval_rag.py --dataset eval/rag_eval_dataset.jsonl --dry-run

    # Run against LangSmith
    LANGSMITH_TRACING=true python scripts/eval_rag.py \
        --dataset eval/rag_eval_dataset.jsonl --run

    # Use a custom evaluator model
    LANGSMITH_EVALUATOR_MODEL=deepseek-v4-flash python scripts/eval_rag.py \
        --dataset eval/rag_eval_dataset.jsonl --run
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

import sqlalchemy.ext.asyncio

# Add project root to path so we can import llm_wiki directly.
project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

# fmt: off
from llm_wiki.application.dto.query_dto import QueryInput  # noqa: E402
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline  # noqa: E402
from llm_wiki.config import settings  # noqa: E402
from llm_wiki.infrastructure.embedding.ollama_adapter import (  # noqa: E402
    OllamaEmbeddingAdapter,
)
from llm_wiki.infrastructure.llm.openai_adapter import OpenAIAdapter  # noqa: E402
from llm_wiki.infrastructure.persistence.postgres.database import (  # noqa: E402
    async_session_factory,
)
from llm_wiki.infrastructure.persistence.redis.cache_adapter import (  # noqa: E402
    RedisCacheAdapter,
)
from llm_wiki.infrastructure.search.pgvector_adapter import PgVectorSearchAdapter  # noqa: E402
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter  # noqa: E402
from llm_wiki.infrastructure.telemetry import create_telemetry_adapter  # noqa: E402
from llm_wiki.infrastructure.telemetry.langsmith_eval_adapter import (  # noqa: E402
    LangSmithEvalAdapter,
    correctness_evaluator,
    faithfulness_evaluator,
    relevance_evaluator,
)


def load_dataset(path: Path) -> list[dict]:
    examples = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            examples.append(json.loads(line))
    return examples


def to_langsmith_examples(examples: list[dict]) -> list[dict]:
    return [
        {
            "inputs": {"question": ex.get("question", "")},
            "outputs": {
                "expected_answer": ex.get("expected_answer", ""),
                "ground_truth_context": ex.get("ground_truth_context", []),
            },
        }
        for ex in examples
    ]


async def build_pipeline() -> QueryPipeline:
    telemetry = create_telemetry_adapter()
    db = async_session_factory()
    embedder = OllamaEmbeddingAdapter(host=settings.ollama_host)
    llm = OpenAIAdapter(
        api_key=settings.opencode_api_key,
        base_url=settings.opencode_base_url,
        model=settings.opencode_primary_model,
    )
    cache = RedisCacheAdapter()
    vector_search = PgVectorSearchAdapter(db)
    keyword_search = TsVectorSearchAdapter(db)
    return QueryPipeline(
        embedder=embedder,
        vector_search=vector_search,
        keyword_search=keyword_search,
        llm=llm,
        cache=cache,
        telemetry=telemetry,
    )


async def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate the RAG pipeline")
    parser.add_argument("--dataset", required=True, type=Path, help="Path to JSONL dataset")
    parser.add_argument(
        "--run", action="store_true", help="Push dataset and run evaluation to LangSmith"
    )
    parser.add_argument("--dry-run", action="store_true", help="Run local stub evaluators only")
    parser.add_argument("--output", type=Path, help="Write metrics JSON to this file")
    parser.add_argument(
        "--dataset-name", default="llm-wiki-rag-eval", help="LangSmith dataset name"
    )
    parser.add_argument("--experiment", default="rag-eval", help="LangSmith experiment prefix")
    args = parser.parse_args()

    if not args.dataset.exists():
        print(f"Dataset not found: {args.dataset}")
        return 1

    examples = load_dataset(args.dataset)
    print(f"Loaded {len(examples)} examples from {args.dataset}")

    if args.dry_run:
        # Run the pipeline locally against each example and apply stub evaluators.
        pipeline = await build_pipeline()
        metrics: dict = {"total_examples": len(examples), "scores": {}}
        for example in examples:
            inputs = example.get("inputs", example)
            question = inputs.get("question", "")
            result = await pipeline.execute(QueryInput(question=question, top_k=10))
            run = {"outputs": result}
            reference = {
                "outputs": {
                    "expected_answer": example.get("expected_answer", ""),
                    "ground_truth_context": example.get("ground_truth_context", []),
                }
            }
            for evaluator in (correctness_evaluator, relevance_evaluator, faithfulness_evaluator):
                eval_result = await evaluator(run, reference)
                metrics["scores"].setdefault(eval_result["key"], []).append(eval_result["score"])

        # Aggregate
        for key, values in metrics["scores"].items():
            clean = [v for v in values if v is not None]
            if clean:
                metrics[f"{key}_mean"] = round(sum(clean) / len(clean), 4)
        del metrics["scores"]
        print(json.dumps(metrics, indent=2, default=str))
        if args.output:
            args.output.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
        return 0

    if not args.run:
        print("Use --dry-run for local evaluation or --run to push to LangSmith.")
        return 1

    if not settings.langsmith_api_key:
        print("LANGSMITH_API_KEY is required for --run")
        return 1

    adapter = LangSmithEvalAdapter()
    dataset_info = adapter.create_dataset(
        name=args.dataset_name,
        description="RAG evaluation dataset for LLM Wiki",
        examples=to_langsmith_examples(examples),
    )
    print("Created dataset:", dataset_info)

    pipeline = await build_pipeline()

    async def target_fn(inputs: dict) -> dict:
        question = inputs.get("question", "")
        result = await pipeline.execute(QueryInput(question=question, top_k=10))
        return {
            "answer": result.get("answer", ""),
            "sources": result.get("sources", []),
            "tokens_used": result.get("tokens_used", 0),
        }

    metrics = adapter.run_evaluation(
        dataset_name=args.dataset_name,
        target_fn=target_fn,
        evaluators=[correctness_evaluator, relevance_evaluator, faithfulness_evaluator],
        experiment_name=args.experiment,
    )
    print(json.dumps(metrics, indent=2, default=str))
    if args.output:
        args.output.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        # Dispose the default async engine if it was created by the script.
        try:
            engine = sqlalchemy.ext.asyncio.create_async_engine(settings.database_url)
            asyncio.run(engine.dispose())
        except Exception:
            pass
