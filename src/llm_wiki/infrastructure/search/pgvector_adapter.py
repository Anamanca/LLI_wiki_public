import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import VectorSearchPort
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.time_range import TimeRange

logger = logging.getLogger(__name__)

RECENCY_LAMBDA = 0.01


class PgVectorSearchAdapter(VectorSearchPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _vector_to_str(self, vector: list[float]) -> str:
        return "[" + ",".join(str(v) for v in vector) + "]"

    def _build_where(
        self, params: dict, source_id: str | None, time_range: TimeRange | None
    ) -> str:
        parts = ["ps.section_vector IS NOT NULL"]
        if source_id:
            parts.append("ps.source_id = :source_id")
            params["source_id"] = source_id
        if time_range:
            parts.append("p.published_at >= :start_date")
            params["start_date"] = time_range.start
            if time_range.end:
                parts.append("p.published_at <= :end_date")
                params["end_date"] = time_range.end
        return " AND ".join(parts)

    async def search_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        vec_str = self._vector_to_str(embedding.vector)
        params: dict = {"vec": vec_str, "limit": top_k}
        where_sql = self._build_where(params, source_id, time_range)

        sql = text(f"""
            SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
                   p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                   p.published_at,
                   (1 - (ps.section_vector <=> :vec)) *
                   EXP(-{RECENCY_LAMBDA} * GREATEST(0,
                       EXTRACT(EPOCH FROM (NOW() - p.published_at)) / 86400.0
                   )) AS similarity
            FROM page_sections ps
            JOIN pages p ON ps.page_id = p.id
            LEFT JOIN sources s ON ps.source_id = s.id
            WHERE {where_sql}
            ORDER BY similarity DESC
            LIMIT :limit
        """)
        result = await self._session.execute(sql, params)

        rows = result.mappings().all()
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
                    "published_at": str(row.get("published_at")) if row.get("published_at") else None,
                },
            )
            for row in rows
        ]

    async def search_sections_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: str | None = None,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        return await self.search_similar(embedding, top_k, source_id, time_range)

    async def search_events_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        time_range: TimeRange | None = None,
    ) -> list[SearchResult]:
        vec_str = self._vector_to_str(embedding.vector)
        params: dict = {"vec": vec_str, "limit": top_k}

        where_parts = ["ec.canonical_embedding IS NOT NULL"]
        if time_range:
            where_parts.append("ec.normalized_date >= :start_date")
            params["start_date"] = time_range.start.date()
            if time_range.end:
                where_parts.append("ec.normalized_date <= :end_date")
                params["end_date"] = time_range.end.date()
        where_sql = " AND ".join(where_parts)

        sql = text(f"""
            SELECT ec.id, ec.title AS content, ec.title,
                   ec.consensus_summary, ec.normalized_date,
                   1 - (ec.canonical_embedding <=> :vec) AS similarity
            FROM event_canonicals ec
            WHERE {where_sql}
            ORDER BY ec.canonical_embedding <=> :vec
            LIMIT :limit
        """)
        result = await self._session.execute(sql, params)
        rows = result.mappings().all()
        return [
            SearchResult(
                content_id=str(row["id"]),
                content_type="event",
                title=row["title"] or "",
                content=row.get("consensus_summary") or "",
                score=float(row["similarity"]),
                metadata={"normalized_date": str(row.get("normalized_date"))},
            )
            for row in rows
        ]
