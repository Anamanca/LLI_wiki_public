from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from llm_wiki.presentation.dependencies import get_db
from llm_wiki.infrastructure.persistence.postgres.repositories.page_repository import PostgresPageRepository
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.domain.value_objects.identifiers import PageId

router = APIRouter()

_ALLOWED_SORT_FIELDS = {"created_at", "updated_at", "published_at"}
_ALLOWED_SORT_ORDERS = {"asc", "desc"}


@router.get("/pages/{slug}")
async def get_page(slug: str, db: AsyncSession = Depends(get_db)):
    repo = PostgresPageRepository(db)
    page = await repo.get_by_slug(slug)
    if not page:
        raise HTTPException(status_code=404, detail="Page not found")

    page_id = UUID(str(page.id.value)) if hasattr(page.id, 'value') else UUID(str(page.id))

    # Fetch sections
    sections_q = (
        select(orm.PageSection)
        .where(orm.PageSection.page_id == page_id)
        .order_by(orm.PageSection.section_order)
    )
    sections_result = await db.execute(sections_q)
    sections = [
        {
            "id": str(s.id),
            "section_order": s.section_order or 0,
            "title": s.title,
            "content_markdown": s.content_markdown,
            "source_ref": s.source_ref,
        }
        for s in sections_result.scalars()
    ]

    # Fetch media assets
    media_q = select(orm.MediaAsset).where(orm.MediaAsset.page_id == page_id).limit(20)
    media_result = await db.execute(media_q)
    media_assets = [
        {
            "id": str(m.id),
            "filename": m.filename,
            "minio_path": m.minio_path,
            "mime_type": m.mime_type,
            "url": None,
            "description": m.description,
        }
        for m in media_result.scalars()
    ]

    # Fetch linked pages
    links_q = (
        select(orm.PageLink, orm.Page)
        .join(orm.Page, orm.PageLink.to_page_id == orm.Page.id)
        .where(orm.PageLink.from_page_id == page_id)
        .limit(20)
    )
    links_result = await db.execute(links_q)
    linked_pages = [
        {
            "id": str(link.id),
            "title": linked_page.title,
            "slug": linked_page.slug,
            "relation_type": link.relation_type,
        }
        for link, linked_page in links_result.all()
    ]

    # Source info
    source_name = None
    source_url = None
    if page.source_id:
        src_result = await db.execute(
            select(orm.Source).where(orm.Source.id == page.source_id.value if hasattr(page.source_id, 'value') else page.source_id)
        )
        src = src_result.scalar_one_or_none()
        if src:
            source_name = src.name
            source_url = src.url

    return {
        "id": str(page.id.value),
        "title": page.title,
        "slug": page.slug,
        "content_markdown": page.content_markdown,
        "summary": page.summary,
        "domain": page.domain,
        "key_entities": page.key_entities,
        "status": page.status,
        "source_name": source_name,
        "source_url": source_url,
        "source_video_url": None,
        "created_at": str(page.created_at) if page.created_at else None,
        "updated_at": str(page.updated_at) if page.updated_at else None,
        "published_at": str(page.published_at) if page.published_at else None,
        "sections": sections,
        "media_assets": media_assets,
        "linked_pages": linked_pages,
    }


@router.get("/pages")
async def list_pages(
    source_id: str | None = None,
    search: str | None = None,
    sort_by: str = Query(default="updated_at", description="Field to sort by"),
    sort_order: str = Query(default="desc", description="Sort direction: asc or desc"),
    page: int = Query(default=1, ge=1, description="Page number (1-indexed)"),
    per_page: int = Query(default=20, ge=1, le=100, description="Items per page"),
    db: AsyncSession = Depends(get_db),
):
    if sort_by not in _ALLOWED_SORT_FIELDS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_by: {sort_by}")
    if sort_order not in _ALLOWED_SORT_ORDERS:
        raise HTTPException(status_code=400, detail=f"Invalid sort_order: {sort_order}")

    repo = PostgresPageRepository(db)
    from llm_wiki.domain.value_objects.identifiers import SourceId

    if source_id:
        pages = await repo.list_by_source(SourceId(UUID(source_id)))
        total = len(pages)
        # Apply search/sort/pagination in memory for source-scoped lists.
        if search:
            pages = [p for p in pages if search.lower() in p.title.lower()]
            total = len(pages)
        reverse = sort_order == "desc"
        pages.sort(key=lambda p: getattr(p, sort_by) or p.created_at, reverse=reverse)
        offset = (page - 1) * per_page
        pages = pages[offset : offset + per_page]
    else:
        offset = (page - 1) * per_page
        pages, total = await repo.list_all(
            limit=per_page,
            offset=offset,
            search=search,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    items = [
        {
            "id": str(p.id.value),
            "title": p.title,
            "slug": p.slug,
            "summary": p.summary,
            "source_name": None,
            "status": p.status,
            "created_at": str(p.created_at) if p.created_at else None,
            "updated_at": str(p.updated_at) if p.updated_at else None,
            "published_at": str(p.published_at) if p.published_at else None,
        }
        for p in pages
    ]
    return {
        "items": items,
        "total": total,
        "page": page,
        "per_page": per_page,
    }
