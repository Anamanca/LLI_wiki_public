FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml + source so pip install . can resolve deps from pyproject.toml
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 curl nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ /app/src/

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "llm_wiki.main:app", "--host", "0.0.0.0", "--port", "8000"]
