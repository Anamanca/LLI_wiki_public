import logging
import re
from typing import Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.application.ports.search.vector_search import KeywordSearchPort
from llm_wiki.domain.value_objects.embedding import SearchResult

logger = logging.getLogger(__name__)


def _clean_query(query_text: str) -> str:
    cleaned = re.sub(
        r"[^\w\sđĐàáảãạâầấẩẫậăằắẳẵặèéẻẽẹêềếểễệìíỉĩịòóỏõọôồốỗổộơờởớỡợùúủũụưừứửữựỳýỷỹỵ]",
        " ",
        query_text,
        flags=re.UNICODE,
    )
    return " ".join(cleaned.split())


class TsVectorSearchAdapter(KeywordSearchPort):
    def __init__(self, session: AsyncSession):
        self._session = session

    async def search_keyword(
        self,
        query: str,
        top_k: int = 10,
    ) -> list[SearchResult]:
        cleaned = _clean_query(query)
        if not cleaned:
            return []

        try:
            sql = text("""
                SELECT ps.id, ps.content_markdown AS content, ps.title AS heading_title,
                       p.title AS page_title, p.slug AS page_slug, s.name AS source_name,
                       ts_rank(ps.fts_vector, plainto_tsquery('simple', :query)) AS similarity,
                       p.published_at
                FROM page_sections ps
                JOIN pages p ON ps.page_id = p.id
                LEFT JOIN sources s ON ps.source_id = s.id
                WHERE ps.fts_vector @@ plainto_tsquery('simple', :query)
                ORDER BY similarity DESC
                LIMIT :limit
            """)
            result = await self._session.execute(
                sql, {"query": cleaned, "limit": top_k}
            )
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
                },
            )
            for row in rows
        ]
