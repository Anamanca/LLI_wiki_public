"""Benchmark the RAG pipeline with tracing on and off.

Usage:
    LANGSMITH_TRACING=false python scripts/benchmark_rag.py \
        --questions eval/questions.jsonl --output metrics/baseline.json
    LANGSMITH_TRACING=true  python scripts/benchmark_rag.py \
        --questions eval/questions.jsonl --output metrics/traced.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path

import sqlalchemy.ext.asyncio

project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project_root))

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


def load_questions(path: Path) -> list[str]:
    questions = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            questions.append(data.get("question", data.get("inputs", {}).get("question", "")))
    return [q for q in questions if q]


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
    parser = argparse.ArgumentParser(description="Benchmark RAG pipeline")
    parser.add_argument(
        "--questions", required=True, type=Path, help="Path to JSONL questions file"
    )
    parser.add_argument("--output", required=True, type=Path, help="Path to write metrics JSON")
    parser.add_argument("--warmup", type=int, default=1, help="Number of warmup questions")
    parser.add_argument("--repeat", type=int, default=1, help="Repeat each question N times")
    args = parser.parse_args()

    if not args.questions.exists():
        print(f"Questions file not found: {args.questions}")
        return 1

    questions = load_questions(args.questions)
    if not questions:
        print("No questions found")
        return 1

    pipeline = await build_pipeline()

    # Warmup
    for i, question in enumerate(questions[: args.warmup]):
        await pipeline.execute(QueryInput(question=question, top_k=10))
        print(f"Warmup {i + 1}/{args.warmup} done")

    records = []
    errors = 0
    tokens_used_total = 0
    cache_hits = 0

    for _ in range(args.repeat):
        for question in questions:
            t0 = time.time()
            try:
                result = await pipeline.execute(QueryInput(question=question, top_k=10))
                latency_ms = (time.time() - t0) * 1000
                tokens_used_total += result.get("tokens_used", 0) or 0
                if result.get("cache_hit"):
                    cache_hits += 1
                pipeline_steps = result.get("pipeline_steps", {})
                records.append(
                    {
                        "question": question,
                        "latency_ms": round(latency_ms, 2),
                        "tokens_used": result.get("tokens_used", 0),
                        "cache_hit": result.get("cache_hit", False),
                        "pipeline_steps": {
                            k: round(v * 1000, 2) for k, v in pipeline_steps.items()
                        },
                    }
                )
            except Exception as exc:
                errors += 1
                records.append(
                    {
                        "question": question,
                        "error": str(exc),
                        "error_type": type(exc).__name__,
                    }
                )

    latencies = [r["latency_ms"] for r in records if "latency_ms" in r]
    metrics = {
        "tracing_enabled": settings.langsmith_tracing_enabled,
        "total_queries": len(records),
        "errors": errors,
        "cache_hits": cache_hits,
        "tokens_used_total": tokens_used_total,
        "latency_ms": {
            "p50": round(statistics.median(latencies), 2) if latencies else None,
            "p95": round(latencies[int(len(latencies) * 0.95)], 2) if latencies else None,
            "mean": round(statistics.mean(latencies), 2) if latencies else None,
            "min": round(min(latencies), 2) if latencies else None,
            "max": round(max(latencies), 2) if latencies else None,
        },
        "step_latency_ms": {},
        "records": records,
    }

    # Aggregate per-step latencies
    step_keys = set()
    for r in records:
        step_keys.update(r.get("pipeline_steps", {}).keys())
    for key in step_keys:
        values = [
            r["pipeline_steps"][key]
            for r in records
            if "pipeline_steps" in r and key in r["pipeline_steps"]
        ]
        if values:
            metrics["step_latency_ms"][key] = {
                "mean": round(statistics.mean(values), 2),
                "p50": round(statistics.median(values), 2),
            }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(metrics, indent=2, default=str), encoding="utf-8")
    print(json.dumps(metrics["latency_ms"], indent=2))
    print(f"Wrote {len(records)} records to {args.output}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    finally:
        try:
            engine = sqlalchemy.ext.asyncio.create_async_engine(settings.database_url)
            asyncio.run(engine.dispose())
        except Exception:
            pass
