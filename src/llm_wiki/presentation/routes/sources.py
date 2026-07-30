from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.domain.entities.source import Source
from llm_wiki.domain.value_objects.identifiers import SourceId
from llm_wiki.infrastructure.persistence.postgres.repositories.source_repository import (
    PostgresSourceRepository,
)
from llm_wiki.presentation.dependencies import get_db
from llm_wiki.presentation.schemas.common import SourceCreateRequest, SourceResponse

router = APIRouter()


@router.post("/sources", response_model=SourceResponse)
async def create_source(
    payload: SourceCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    repo = PostgresSourceRepository(db)
    source = Source(
        id=SourceId(UUID(bytes=__import__("os").urandom(16))),
        name=payload.name,
        platform=payload.platform,
        external_id=payload.external_id,
        url=payload.url,
        config=payload.config if hasattr(payload, "config") and payload.config else {},
    )
    result = await repo.save(source)
    return SourceResponse(
        id=str(result.id.value),
        name=result.name,
        platform=result.platform,
        external_id=result.external_id,
        url=result.url,
        status=result.status,
        config=result.config if result.config else {},
        added_at=str(result.added_at) if result.added_at else None,
        last_checked_at=str(result.last_checked_at) if result.last_checked_at else None,
        last_video_published_at=str(result.last_video_published_at)
        if result.last_video_published_at
        else None,
    )


@router.get("/sources")
async def list_sources(db: AsyncSession = Depends(get_db)):
    repo = PostgresSourceRepository(db)
    sources = await repo.list_active()
    items = [
        SourceResponse(
            id=str(s.id.value),
            name=s.name,
            platform=s.platform,
            external_id=s.external_id,
            url=s.url,
            status=s.status,
            config=s.config if s.config else {},
            added_at=str(s.added_at) if s.added_at else None,
            last_checked_at=str(s.last_checked_at) if s.last_checked_at else None,
            last_video_published_at=str(s.last_video_published_at)
            if s.last_video_published_at
            else None,
        )
        for s in sources
    ]
    return {"sources": items, "total": len(items)}
