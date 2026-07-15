#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "=== Installing backend dependencies ==="
pip install -e "${PROJECT_DIR}[dev]"

echo ""
echo "=== Port-forwarding K8s services ==="
echo "Run these in separate terminals:"
echo ""
echo "  kubectl port-forward -n llm-wiki svc/postgres 5432:5432"
echo "  kubectl port-forward -n llm-wiki svc/redis 6379:6379"
echo "  kubectl port-forward -n llm-wiki svc/ollama 11434:11434"
echo "  kubectl port-forward -n llm-wiki svc/minio 9000:9000"
echo ""
echo "Then create .env with localhost URLs:"
echo "  DATABASE_URL=postgresql+asyncpg://wiki:0comatkhau@localhost:5432/llm_wiki"
echo "  REDIS_HOST=localhost"
echo "  OLLAMA_HOST=http://localhost:11434"
echo "  MINIO_ENDPOINT=localhost:9000"
echo ""

echo "=== Starting backend ==="
cd "$PROJECT_DIR"
PYTHONPATH="${PROJECT_DIR}/src:${PROJECT_DIR}" uvicorn llm_wiki.main:app --reload --host 0.0.0.0 --port 8000 &
BACKEND_PID=$!

echo ""
echo "=== Starting frontend dev server ==="
echo "In another terminal:"
echo "  cd ${PROJECT_DIR}/frontend"
echo "  npm install"
echo "  echo 'NEXT_PUBLIC_API_URL=http://localhost:8000/api' > .env.local"
echo "  npm run dev"
echo ""
echo "Frontend will be at http://localhost:3000"
echo ""
echo "Press Ctrl+C to stop backend"
wait $BACKEND_PID
