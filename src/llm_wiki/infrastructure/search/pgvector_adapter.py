import logging
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import VectorSearchPort
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult

logger = logging.getLogger(__name__)


class PgVectorSearchAdapter(VectorSearchPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    def _vector_to_str(self, vector: list[float]) -> str:
        return "[" + ",".join(str(v) for v in vector) + "]"

    async def search_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: Optional[str] = None,
    ) -> list[SearchResult]:
        vec_str = self._vector_to_str(embedding.vector)

        if source_id:
            sql = text("""
                SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
                       p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                       1 - (ps.section_vector <=> :vec) AS similarity
                FROM page_sections ps
                JOIN pages p ON ps.page_id = p.id
                JOIN sources s ON ps.source_id = s.id
                WHERE ps.source_id = :source_id
                  AND ps.section_vector IS NOT NULL
                ORDER BY ps.section_vector <=> :vec
                LIMIT :limit
            """)
            result = await self._session.execute(
                sql, {"vec": vec_str, "source_id": source_id, "limit": top_k}
            )
        else:
            sql = text("""
                SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
                       p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                       1 - (ps.section_vector <=> :vec) AS similarity
                FROM page_sections ps
                JOIN pages p ON ps.page_id = p.id
                LEFT JOIN sources s ON ps.source_id = s.id
                WHERE ps.section_vector IS NOT NULL
                ORDER BY ps.section_vector <=> :vec
                LIMIT :limit
            """)
            result = await self._session.execute(sql, {"vec": vec_str, "limit": top_k})

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
                },
            )
            for row in rows
        ]

    async def search_sections_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
        source_id: Optional[str] = None,
    ) -> list[SearchResult]:
        return await self.search_similar(embedding, top_k, source_id)

    async def search_events_similar(
        self,
        embedding: Embedding,
        top_k: int = 10,
    ) -> list[SearchResult]:
        vec_str = self._vector_to_str(embedding.vector)
        sql = text("""
            SELECT ec.id, ec.title AS content, ec.title,
                   ec.consensus_summary, ec.normalized_date,
                   1 - (ec.canonical_embedding <=> :vec) AS similarity
            FROM event_canonicals ec
            WHERE ec.canonical_embedding IS NOT NULL
            ORDER BY ec.canonical_embedding <=> :vec
            LIMIT :limit
        """)
        result = await self._session.execute(sql, {"vec": vec_str, "limit": top_k})
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
