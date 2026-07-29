"""Pgvector event search adapter — vector + keyword search over event_observations."""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.event_search_port import EventSearchPort
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange

logger = logging.getLogger(__name__)

RECENCY_LAMBDA = 0.01  # half-life ~69 days — use as default; override via constructor


class PgVectorEventSearchAdapter(EventSearchPort):
    """Searches ``event_observations`` via pgvector + PostgreSQL tsvector.

    JOINs ``event_canonicals`` for consensus summaries, ``pages`` for context,
    and ``sources`` for provenance.  Applies recency decay identical to the
    production 29 pipeline.
    """

    def __init__(self, session: AsyncSession, recency_lambda: float | None = None):
        self._session = session
        self._recency_lambda = recency_lambda if recency_lambda is not None else RECENCY_LAMBDA

    @staticmethod
    def _vector_to_str(vector: list[float]) -> str:
        return "[" + ",".join(str(v) for v in vector) + "]"

    def _build_event_where(
        self, params: dict, time_range: TimeRange | None
    ) -> str:
        """Build WHERE clause for event observation SQL queries.

        Filters on ``ec.normalized_date`` (the actual event occurrence date)
        rather than ``eo.source_published_at`` (article publication date).
        A user asking about "July 2025" should see events that happened in
        July 2025, regardless of when the source article was published.
        """
        parts = ["eo.embedding IS NOT NULL"]
        if time_range:
            parts.append("ec.normalized_date >= :start_date")
            params["start_date"] = time_range.start.date()
            if time_range.end:
                parts.append("ec.normalized_date <= :end_date")
                params["end_date"] = time_range.end.date()
        return " AND ".join(parts)

    def _build_kw_where(
        self, params: dict, time_range: TimeRange | None
    ) -> str:
        """Build WHERE clause for event keyword SQL queries.

        Same date semantics as ``_build_event_where``: filters on the actual
        event date (ec.normalized_date), not the article publication date.
        """
        parts = ["eo.fts_vector IS NOT NULL"]
        if time_range:
            parts.append("ec.normalized_date >= :start_date")
            params["start_date"] = time_range.start.date()
            if time_range.end:
                parts.append("ec.normalized_date <= :end_date")
                params["end_date"] = time_range.end.date()
        return " AND ".join(parts)

    @staticmethod
    def _row_to_search_result(row) -> SearchResult:
        """Convert a DB row to a SearchResult.

        Uses ``ec.consensus_summary`` as the primary content when available —
        it is a curated, multi-observation synthesis that provides more
        information per token than a single raw ``eo.description``.
        Falls back to ``eo.description`` when consensus is missing.
        """
        consensus = row.get("consensus_summary")
        raw_description = row.get("content") or ""
        primary_content = (consensus or raw_description or "").strip()
        stance = row.get("stance")
        obs_count = row.get("observation_count")

        # Compact credential line: (số quan sát: N, quan điểm: stance)
        credential_parts = []
        if obs_count is not None:
            credential_parts.append(f"số quan sát: {obs_count}")
        if stance:
            credential_parts.append(f"quan điểm: {stance}")
        credential = f" ({'; '.join(credential_parts)})" if credential_parts else ""

        normalized_date = row.get("event_date")  # ec.normalized_date
        event_date_str = str(row.get("event_date")) if row.get("event_date") else None

        return SearchResult(
            content_id=str(row["id"]),
            content_type="event_observation",
            title=row.get("heading_title") or row.get("page_title") or "",
            content=f"{primary_content}{credential}",
            score=float(row["similarity"]),
            metadata={
                "page_title": row.get("page_title"),
                "page_slug": row.get("page_slug"),
                "source_name": row.get("source_name"),
                "published_at": str(row.get("published_at")) if row.get("published_at") else None,
                "event_date": event_date_str,
                # normalized_date is the canonical event occurrence date —
                # used by post-RRF hard-filtering in the pipeline
                "normalized_date": event_date_str,
                "event_canonical_id": str(row.get("event_canonical_id")) if row.get("event_canonical_id") else None,
                "consensus_summary": row.get("consensus_summary"),
                "observation_count": row.get("observation_count"),
                "stance": row.get("stance"),
                "sentiment_score": float(row["sentiment_score"]) if row.get("sentiment_score") is not None else None,
            },
        )

    async def search_events(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        if not embedding.vector or all(v == 0.0 for v in embedding.vector):
            return []

        vec_str = self._vector_to_str(embedding.vector)
        params: dict = {"vec": vec_str, "limit": top_k}
        where_sql = self._build_event_where(params, time_range)

        sql = text(f"""
            SELECT eo.id, eo.description AS content, ec.title AS heading_title,
                   p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                   eo.source_published_at AS published_at,
                   ec.normalized_date AS event_date,
                   ec.id AS event_canonical_id, ec.consensus_summary,
                   ec.observation_count,
                   eo.stance, eo.sentiment_score,
                   (1 - (eo.embedding <=> :vec)) *
                   EXP(-{self._recency_lambda} * GREATEST(0,
                       EXTRACT(EPOCH FROM (NOW() - eo.source_published_at)) / 86400.0
                   )) AS similarity
            FROM event_observations eo
            JOIN event_canonicals ec ON eo.event_id = ec.id
            LEFT JOIN pages p ON eo.page_id = p.id
            LEFT JOIN sources s ON eo.source_id = s.id
            WHERE {where_sql}
            ORDER BY similarity DESC
            LIMIT :limit
        """)
        result = await self._session.execute(sql, params)
        rows = result.mappings().all()
        return [self._row_to_search_result(r) for r in rows]

    async def search_events_keyword(
        self,
        query: str,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        if not query or not query.strip():
            return []

        from llm_wiki.infrastructure.search.tsvector_adapter import _clean_query
        cleaned = _clean_query(query)

        # Primary: plainto_tsquery (AND logic, high precision)
        results = await self._search_events_kw_with_query(
            cleaned, top_k, time_range, use_plainto=True,
        )

        # Fallback: if primary returns 0 and query has multiple terms,
        # try OR'd terms for better recall
        if not results and len(cleaned.split()) > 1:
            from llm_wiki.infrastructure.search.tsvector_adapter import _build_or_query
            or_query = _build_or_query(cleaned)
            logger.debug(
                "Event keyword search returned 0 for %r, falling back to OR: %r",
                cleaned[:80], or_query[:80],
            )
            results = await self._search_events_kw_with_query(
                or_query, top_k, time_range, use_plainto=False,
            )

        return results

    async def _search_events_kw_with_query(
        self,
        query: str,
        top_k: int,
        time_range: TimeRange | None,
        use_plainto: bool = True,
    ) -> list[SearchResult]:
        """Execute a single event keyword search with the specified query type.

        Args:
            query: Cleaned query (or OR'd terms for fallback).
            top_k: Max results.
            time_range: Optional time filter on ec.normalized_date.
            use_plainto: Use ``plainto_tsquery`` (AND) or ``to_tsquery`` (OR).
        """
        params: dict = {"query": query, "limit": top_k}
        where_sql = self._build_kw_where(params, time_range)

        if use_plainto:
            query_func = "plainto_tsquery"
        else:
            query_func = "to_tsquery"

        sql = text(f"""
            SELECT eo.id, eo.description AS content, ec.title AS heading_title,
                   p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                   eo.source_published_at AS published_at,
                   ec.normalized_date AS event_date,
                   ec.id AS event_canonical_id, ec.consensus_summary,
                   ec.observation_count,
                   eo.stance, eo.sentiment_score,
                   ts_rank(eo.fts_vector, {query_func}('simple', :query)) *
                   EXP(-{self._recency_lambda} * GREATEST(0,
                       EXTRACT(EPOCH FROM (NOW() - eo.source_published_at)) / 86400.0
                   )) AS similarity
            FROM event_observations eo
            JOIN event_canonicals ec ON eo.event_id = ec.id
            LEFT JOIN pages p ON eo.page_id = p.id
            LEFT JOIN sources s ON eo.source_id = s.id
            WHERE {where_sql}
              AND eo.fts_vector @@ {query_func}('simple', :query)
            ORDER BY similarity DESC
            LIMIT :limit
        """)
        result = await self._session.execute(sql, params)
        rows = result.mappings().all()
        return [self._row_to_search_result(r) for r in rows]
