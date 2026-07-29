FROM python:3.12-slim AS builder

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev curl libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy pyproject.toml + source so pip install . can resolve deps from pyproject.toml
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir .

FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libcurl4 libssl3 curl nodejs unzip \
    && rm -rf /var/lib/apt/lists/*

# Install deno — yt-dlp's default JS runtime for YouTube JS challenges
# yt-dlp >= 2025 requires a JS runtime (deno or node); deno is preferred
# because it's enabled by default and is a single self-contained binary
RUN curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip" \
    -o /tmp/deno.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && deno --version

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src/ /app/src/

ENV PYTHONPATH=/app/src:/app

EXPOSE 8000

CMD ["uvicorn", "llm_wiki.main:app", "--host", "0.0.0.0", "--port", "8000"]
