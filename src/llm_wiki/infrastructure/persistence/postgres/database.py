from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session

from llm_wiki.config import settings

_async_engine = create_async_engine(
    settings.database_url,
    echo=False,
    pool_size=20,
    max_overflow=10,
)

async_session_factory = async_sessionmaker(
    _async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
)

_sync_engine = create_engine(
    settings.database_url.replace("postgresql+asyncpg://", "postgresql+psycopg2://"),
    echo=False,
    pool_size=5,
)

SyncSessionLocal = sessionmaker(
    bind=_sync_engine,
    class_=Session,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncSession:
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_async_session() -> AsyncSession:
    async with async_session_factory() as session:
        yield session
