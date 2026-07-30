import os

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

TEST_DATABASE_URL = os.getenv(
    "DATABASE_URL", "postgresql+asyncpg://postgres:test@localhost:5432/test"
)


@pytest.fixture(scope="session")
def test_engine():
    engine = create_async_engine(TEST_DATABASE_URL, echo=False)
    yield engine


@pytest.fixture
async def db_session(test_engine):
    async with test_engine.connect() as conn:
        await conn.begin()
        factory = async_sessionmaker(test_engine, class_=AsyncSession, expire_on_commit=False)
        async with factory(bind=conn) as session:
            yield session
        await conn.rollback()
