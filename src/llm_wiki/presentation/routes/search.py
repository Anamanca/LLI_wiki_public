from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter
from llm_wiki.presentation.dependencies import get_db

router = APIRouter()


@router.get("/search")
async def search(
    q: str = Query(..., description="Search query"),
    db: AsyncSession = Depends(get_db),
):
    keyword = TsVectorSearchAdapter(db)
    results = await keyword.search_keyword(q, top_k=10)

    result_ids = [r.content_id for r in results]
    enriched = {}
    if result_ids:
        from uuid import UUID

        uuids = []
        for rid in result_ids:
            try:
                uuids.append(UUID(rid))
            except ValueError:
                pass
        if uuids:
            sections_q = (
                select(orm.PageSection, orm.Page, orm.Source)
                .join(orm.Page, orm.PageSection.page_id == orm.Page.id, isouter=True)
                .join(orm.Source, orm.Page.source_id == orm.Source.id, isouter=True)
                .where(orm.PageSection.id.in_(uuids))
            )
            sec_result = await db.execute(sections_q)
            for section, page, source in sec_result.all():
                enriched[str(section.id)] = {
                    "slug": page.slug if page else "",
                    "summary": page.summary[:200] if page and page.summary else None,
                    "source_name": source.name if source else None,
                    "published_at": str(page.published_at) if page and page.published_at else None,
                }

    return {
        "results": [
            {
                "id": r.content_id,
                "title": r.title,
                "slug": enriched.get(r.content_id, {}).get("slug", ""),
                "summary": enriched.get(r.content_id, {}).get("summary"),
                "source_name": enriched.get(r.content_id, {}).get("source_name"),
                "published_at": enriched.get(r.content_id, {}).get("published_at"),
            }
            for r in results
        ],
        "total": len(results),
    }
