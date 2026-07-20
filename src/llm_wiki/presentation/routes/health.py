from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from llm_wiki.infrastructure.persistence.postgres import models as orm
from llm_wiki.presentation.dependencies import get_db

router = APIRouter()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db)):
    pending_q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status.in_(["pending", "pending_transcribe"])
    )
    pending_count = (await db.execute(pending_q)).scalar() or 0
    req_mem_q = select(func.count(orm.SourceItem.id)).where(
        orm.SourceItem.status == "requires_membership"
    )
    req_count = (await db.execute(req_mem_q)).scalar() or 0
    failed_q = select(func.count(orm.SourceItem.id)).where(orm.SourceItem.status == "failed")
    failed_count = (await db.execute(failed_q)).scalar() or 0

    return {
        "status": "ok",
        "version": "2.0.0",
        "db": "connected",
        "pending_count": pending_count,
        "requires_membership_count": req_count,
        "failed_count": failed_count,
    }
