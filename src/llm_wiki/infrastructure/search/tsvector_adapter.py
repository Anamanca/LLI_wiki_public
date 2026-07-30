import logging
import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import KeywordSearchPort
from llm_wiki.domain.value_objects.embedding import SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange

logger = logging.getLogger(__name__)

RECENCY_LAMBDA = 0.01


def _clean_query(query_text: str) -> str:
    cleaned = re.sub(
        r"[^\w\sđĐàáảãạâầấẩẫậăằắẳẵặèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốỗổộơờởớỡợùúủũụưừứửữựỳýỷỹỵ]",
        " ",
        query_text,
        flags=re.UNICODE,
    )
    return " ".join(cleaned.split())


def _build_or_query(query: str) -> str:
    """Convert a multi-word query into OR'd individual terms for fallback.

    ``plainto_tsquery`` uses AND logic (word1 & word2 & word3) which can
    return zero results for long queries.  This fallback joins terms with
    ``|`` so a document matching *any* term is returned.
    """
    terms = query.split()
    if len(terms) <= 1:
        return query
    return " | ".join(terms)


class TsVectorSearchAdapter(KeywordSearchPort):
    def __init__(self, session: AsyncSession, recency_lambda: float | None = None):
        self._session = session
        self._recency_lambda = recency_lambda if recency_lambda is not None else RECENCY_LAMBDA

    async def search_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        cleaned = _clean_query(query)
        if not cleaned:
            return []

        # Primary: plainto_tsquery (AND logic, high precision)
        results = await self._search_with_query(cleaned, top_k, time_range)

        # Fallback: if primary returns 0, try OR'd terms for recall
        if not results and len(cleaned.split()) > 1:
            or_query = _build_or_query(cleaned)
            logger.debug(
                "Keyword search returned 0 results for %r, falling back to OR query: %r",
                cleaned[:80],
                or_query[:80],
            )
            results = await self._search_with_query(
                or_query,
                top_k,
                time_range,
                use_plainto=False,
            )

        return results

    async def _search_with_query(
        self,
        query: str,
        top_k: int,
        time_range: TimeRange | None,
        use_plainto: bool = True,
    ) -> list[SearchResult]:
        """Execute a single keyword search with the specified query function.

        Args:
            query: The cleaned query string (or OR'd terms for fallback).
            top_k: Max results to return.
            time_range: Optional time filter.
            use_plainto: If True, use ``plainto_tsquery`` (AND logic).
                         If False, use ``to_tsquery`` (for OR'd terms).
        """
        params: dict = {"query": query, "limit": top_k}
        where_parts: list[str]
        if use_plainto:
            where_parts = ["ps.fts_vector @@ plainto_tsquery('simple', :query)"]
            rank_expr = "ts_rank(ps.fts_vector, plainto_tsquery('simple', :query))"
        else:
            where_parts = ["ps.fts_vector @@ to_tsquery('simple', :query)"]
            rank_expr = "ts_rank(ps.fts_vector, to_tsquery('simple', :query))"

        if time_range:
            where_parts.append("p.published_at >= :start_date")
            params["start_date"] = time_range.start
            if time_range.end:
                where_parts.append("p.published_at <= :end_date")
                params["end_date"] = time_range.end
        where_sql = " AND ".join(where_parts)

        try:
            sql = text(f"""
                SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
                       p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                       {rank_expr} *
                       EXP(-{self._recency_lambda} * GREATEST(0,
                           EXTRACT(EPOCH FROM (NOW() - p.published_at)) / 86400.0
                       )) AS similarity,
                       p.published_at
                FROM page_sections ps
                JOIN pages p ON ps.page_id = p.id
                LEFT JOIN sources s ON ps.source_id = s.id
                WHERE {where_sql}
                ORDER BY similarity DESC
                LIMIT :limit
            """)
            result = await self._session.execute(sql, params)
            rows = result.mappings().all()
        except Exception:
            logger.debug("Keyword search unavailable (fts_vector column missing?)")
            return []

        return [
            SearchResult(
                content_id=str(row["id"]),
                content_type="page_section",
                title=row.get("heading_title") or row.get("page_title") or "",
                content=row["content"] or "",
                score=float(row["similarity"]),
                metadata={
                    "page_title": row.get("page_title"),
                    "page_slug": row.get("page_slug"),
                    "source_name": row.get("source_name"),
                    "published_at": str(row.get("published_at"))
                    if row.get("published_at")
                    else None,
                },
            )
            for row in rows
        ]
