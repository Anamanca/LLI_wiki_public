"""Cross-encoder re-ranker using BAAI/bge-reranker-v2-m3 running on local CPU.

Replaces the LLM-based reranker with a dedicated cross-encoder model that
runs inference locally — no API calls, lower latency, zero per-query cost.

The model is loaded once at init time (~1.1 GB RAM) and reused across requests.
Uses ``sentence-transformers`` CrossEncoder for broader compatibility.
"""

from __future__ import annotations

import logging
import time

from llm_wiki.application.ports.search.reranker_port import RerankerPort
from llm_wiki.domain.value_objects.embedding import SearchResult

logger = logging.getLogger(__name__)

# ── Cap document content to avoid blowing up model input ──────────────────
_MAX_CONTENT_CHARS = 500
# Default batch size for CPU inference — balanced for E2286M (8C/16T)
_DEFAULT_BATCH_SIZE = 16


class CrossEncoderRerankerAdapter(RerankerPort):
    """Re-rank documents using a local cross-encoder model.

    Uses ``BAAI/bge-reranker-v2-m3`` (560M params) via sentence-transformers.
    The model runs on CPU — no GPU required.  Inference takes ~200-500 ms
    for a batch of 15-20 documents on a modern Xeon CPU.

    Falls back to original ranking on any failure so the pipeline never
    breaks due to reranker issues.
    """

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        batch_size: int = _DEFAULT_BATCH_SIZE,
        max_length: int = 512,
    ):
        """Initialise the cross-encoder and load model weights.

        Args:
            model_name: HuggingFace model ID or local path.
            batch_size: Number of (query, doc) pairs scored per inference call.
            max_length: Max token length per (query, doc) pair.
        """
        self._model_name = model_name
        self._batch_size = batch_size
        self._max_length = max_length
        self._model = None  # lazy-loaded on first rerank call

    async def rerank(
        self,
        query: str,
        documents: list[SearchResult],
        top_n: int = 20,
    ) -> list[SearchResult]:
        """Score each document against *query* and return the top *top_n*.

        Returns original documents in cross-encoder score order.
        Falls back to the input order (truncated to *top_n*) on any error.
        """
        if not documents:
            return []
        if len(documents) <= 2:
            return documents[:top_n]

        # Lazy-load model on first call so app startup is not blocked
        if self._model is None:
            self._model = self._load_model()

        try:
            return self._rerank_impl(query, documents, top_n)
        except Exception:
            logger.warning(
                "Cross-encoder rerank failed, returning original order",
                exc_info=True,
            )
            return documents[:top_n]

    # ── internal helpers ────────────────────────────────────────────────

    def _load_model(self):
        """Load the sentence-transformers CrossEncoder model.

        Lazy-loaded on first rerank call so app startup is not blocked.
        """
        t0 = time.time()
        logger.info(
            "Loading cross-encoder model '%s' …",
            self._model_name,
        )
        from sentence_transformers import CrossEncoder

        model = CrossEncoder(
            self._model_name,
            max_length=self._max_length,
        )
        logger.info(
            "Cross-encoder model loaded in %.1f s",
            time.time() - t0,
        )
        return model

    def _rerank_impl(
        self,
        query: str,
        documents: list[SearchResult],
        top_n: int,
    ) -> list[SearchResult]:
        """Core reranking logic (sync, called from async wrapper)."""
        # Build (query, content) pairs
        pairs = [
            [query, (doc.content or "")[:_MAX_CONTENT_CHARS].replace("\n", " ")]
            for doc in documents
        ]

        # predict() handles batching internally and returns a numpy array of scores
        all_scores = self._model.predict(
            pairs,
            batch_size=self._batch_size,
            show_progress_bar=False,
        )

        # Pair scores with documents and sort descending
        scored: list[tuple[float, int, SearchResult]] = []
        for idx, (doc, score) in enumerate(zip(documents, all_scores, strict=False)):
            scored.append((float(score), idx, doc))

        scored.sort(key=lambda x: x[0], reverse=True)

        result = [doc for _, _, doc in scored[:top_n]]
        if result and scored:
            logger.debug(
                "Cross-encoder reranked %d docs → %d (top: %.4f, bottom: %.4f)",
                len(documents),
                len(result),
                scored[0][0],
                scored[-1][0],
            )
        return result
