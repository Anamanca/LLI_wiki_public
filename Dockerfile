FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir \
        fastapi>=0.115 \
        "uvicorn[standard]>=0.30" \
        "sqlalchemy[asyncio]>=2.0" \
        asyncpg>=0.29 \
        psycopg2-binary>=2.9 \
        pgvector>=0.3 \
        "redis[hiredis]>=5.0" \
        httpx \
        pydantic>=2.0 \
        pydantic-settings>=2.5 \
        dependency_injector>=4.0 \
        alembic>=1.13 \
        minio \
        "python-telegram-bot>=21.0" \
        "yt-dlp>=2024.0" \
        "faster-whisper>=1.0" \
        ollama>=0.4 \
        psutil>=5.9 \
        python-dotenv \
        langsmith>=0.1.0

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ /app/src/

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "llm_wiki.main:app", "--host", "0.0.0.0", "--port", "8000"]
