from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from llm_wiki.presentation.dependencies import get_db
from llm_wiki.infrastructure.persistence.postgres.repositories.page_repository import PostgresPageRepository
from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.domain.value_objects.identifiers import PageId

router = APIRouter()


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
async def list_pages(source_id: str | None = None, limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)):
    repo = PostgresPageRepository(db)
    from llm_wiki.domain.value_objects.identifiers import SourceId
    if source_id:
        pages = await repo.list_by_source(SourceId(UUID(source_id)))
    else:
        pages = await repo.list_all(limit=limit, offset=offset)
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
        "total": len(items),
        "page": (offset // limit) + 1 if limit > 0 else 1,
        "per_page": limit,
    }
