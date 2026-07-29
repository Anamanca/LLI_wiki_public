"""LLM-based cross-encoder re-ranker using the primary LLM.

Uses the same LLM as the synthesis step to score document relevance.
A dedicated cross-encoder model (e.g., bge-reranker-v2) would be faster
and cheaper, but this adapter requires zero additional infrastructure.
"""

import json
import logging

from llm_wiki.application.ports.search.reranker_port import RerankerPort
from llm_wiki.application.ports.search.vector_search import LLMClientPort
from llm_wiki.domain.value_objects.embedding import SearchResult

logger = logging.getLogger(__name__)

RERANK_SYSTEM_PROMPT = """Bạn là trợ lý đánh giá mức độ liên quan của tài liệu.
Với mỗi tài liệu, cho điểm từ 0.0 đến 10.0 dựa trên mức độ liên quan với câu hỏi.

Tiêu chí:
- 9-10: Tài liệu trả lời trực tiếp câu hỏi
- 7-8: Tài liệu có thông tin liên quan nhiều
- 5-6: Tài liệu có một số điểm liên quan
- 3-4: Tài liệu đề cập chủ đề nhưng không trả lời câu hỏi
- 0-2: Tài liệu không liên quan

Output JSON array: [{"id": "...", "score": 0.0, "reason": "..."}]
CHỈ output JSON, không markdown."""


class LLMRerankerAdapter(RerankerPort):
    """Re-rank documents using an LLM scoring pass.

    Sends documents in batches to avoid exceeding context limits.
    Falls back to the original ranking on any failure.
    """

    def __init__(
        self,
        llm: LLMClientPort,
        batch_size: int = 15,
        temperature: float = 0.0,
        max_tokens: int = 500,
    ):
        self._llm = llm
        self._batch_size = batch_size
        self._temperature = temperature
        self._max_tokens = max_tokens

    async def rerank(
        self,
        query: str,
        documents: list[SearchResult],
        top_n: int = 20,
    ) -> list[SearchResult]:
        if not documents:
            return []
        if len(documents) <= 2:
            return documents[:top_n]

        try:
            all_scores: dict[str, tuple[float, SearchResult]] = {}

            # Process in batches
            for i in range(0, len(documents), self._batch_size):
                batch = documents[i : i + self._batch_size]
                batch_scores = await self._score_batch(query, batch)
                for doc_id, (score, doc) in batch_scores.items():
                    if doc_id not in all_scores or score > all_scores[doc_id][0]:
                        all_scores[doc_id] = (score, doc)

            # Sort by score descending, return top_n
            sorted_docs = sorted(all_scores.values(), key=lambda x: x[0], reverse=True)
            result = [doc for _, doc in sorted_docs[:top_n]]

            if result:
                logger.debug(
                    "Re-ranked %d docs → %d (top score: %.2f, bottom: %.2f)",
                    len(documents), len(result),
                    sorted_docs[0][0] if sorted_docs else 0,
                    sorted_docs[-1][0] if sorted_docs else 0,
                )
            return result

        except Exception:
            logger.debug("Re-ranking failed, returning original order")
            return documents[:top_n]

    async def _score_batch(
        self, query: str, documents: list[SearchResult],
    ) -> dict[str, tuple[float, SearchResult]]:
        """Score a batch of documents."""
        doc_list = []
        for idx, doc in enumerate(documents):
            content_preview = (doc.content or "")[:500].replace("\n", " ")
            doc_list.append({
                "id": str(idx),
                "title": doc.title or "",
                "content": content_preview,
            })

        user_prompt = (
            f"Câu hỏi: {query}\n\n"
            f"Tài liệu cần đánh giá:\n{json.dumps(doc_list, ensure_ascii=False)}"
        )

        messages = [
            {"role": "system", "content": RERANK_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]

        raw = await self._llm.chat_completion(
            messages=messages,
            temperature=self._temperature,
            max_tokens=self._max_tokens,
        )

        scores = json.loads(raw.strip())
        if not isinstance(scores, list):
            return {}

        result: dict[str, tuple[float, SearchResult]] = {}
        for item in scores:
            if not isinstance(item, dict):
                continue
            idx = int(item.get("id", -1))
            score = float(item.get("score", 0))
            if 0 <= idx < len(documents):
                doc = documents[idx]
                result[doc.content_id] = (score, doc)

        return result
