# ── Base stage: system deps + torch CPU (shared) ──────────────────────────
FROM python:3.12-slim AS base

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 libcurl4 libssl3 curl nodejs unzip ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Install deno — yt-dlp's default JS runtime for YouTube JS challenges
RUN curl -fsSL "https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip" \
    -o /tmp/deno.zip \
    && unzip -o /tmp/deno.zip -d /usr/local/bin \
    && chmod +x /usr/local/bin/deno \
    && rm /tmp/deno.zip \
    && deno --version

WORKDIR /app

# ── Builder stage: compile deps ────────────────────────────────────────────
FROM base AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev libcurl4-openssl-dev libssl-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
# setuptools needs src/ to exist (packages.find where = ["src"])
COPY src/ ./src/

# Pre-install torch CPU-only BEFORE pip install . so sentence-transformers
# finds torch already satisfied and does NOT pull torch+cu130 + nvidia-*.
# Uses the official PyTorch CPU index (no CUDA → no nvidia-* packages).
RUN pip install --no-cache-dir --upgrade pip setuptools wheel \
    && pip install --no-cache-dir \
        --index-url https://download.pytorch.org/whl/cpu \
        torch \
    && pip install --no-cache-dir .[api,worker]


# ── API image (backend-v2) ─────────────────────────────────────────────────
FROM base AS api

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ /app/src/

ENV PYTHONPATH=/app/src:/app
EXPOSE 8000

CMD ["uvicorn", "llm_wiki.main:app", "--host", "0.0.0.0", "--port", "8000"]


# ── Worker image (cpu-worker + wiki-consumer) ──────────────────────────────
FROM base AS worker

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY src/ /app/src/

ENV PYTHONPATH=/app/src:/app
# No default CMD — set per-service via K8s command override
