#!/usr/bin/env bash
#
# Sync changed source files to K8s pod and run tests.
# Backend: kubectl cp + uvicorn --reload (instant)
# Frontend: kind load + pod restart (rebuild image with dev mode)
#
# Usage:
#   ./scripts/sync-and-test.sh              # sync all + run tests
#   ./scripts/sync-and-test.sh --backend    # sync backend only
#   ./scripts/sync-and-test.sh --frontend   # sync frontend only
#   ./scripts/sync-and-test.sh --test-only  # just run tests
#
set -euo pipefail

NAMESPACE="llm-wiki"
APP="backend-v2"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

sync_backend() {
    local pod
    pod=$(kubectl get pods -n "$NAMESPACE" -l app="$APP" -o jsonpath='{.items[0].metadata.name}')
    if [ -z "$pod" ]; then
        echo "ERROR: No pod found for app=$APP"
        return 1
    fi
    echo "Pod: $pod"

    local files=(
        src/llm_wiki/presentation/routes/health.py
        src/llm_wiki/presentation/routes/sources.py
        src/llm_wiki/presentation/routes/pages.py
        src/llm_wiki/presentation/routes/search.py
        src/llm_wiki/presentation/routes/query.py
        src/llm_wiki/presentation/routes/stubs.py
        src/llm_wiki/presentation/schemas/common.py
        src/llm_wiki/presentation/middleware/error_handler.py
        src/llm_wiki/presentation/middleware/request_logging.py
        src/llm_wiki/presentation/dependencies.py
        src/llm_wiki/infrastructure/persistence/postgres/database.py
        src/llm_wiki/infrastructure/persistence/postgres/repositories/source_repository.py
        src/llm_wiki/infrastructure/persistence/postgres/repositories/page_repository.py
    )

    for f in "${files[@]}"; do
        if [ -f "$PROJECT_DIR/$f" ]; then
            echo "  Syncing: $f"
            kubectl cp "$PROJECT_DIR/$f" "$NAMESPACE/$pod:/app/$f"
        fi
    done

    echo "Backend synced. Uvicorn --reload will detect changes automatically."
    sleep 2
}

sync_frontend() {
    echo "Syncing frontend..."

    # Check for changed source files
    local changed_files=$(find "$PROJECT_DIR/frontend" -name "*.tsx" -o -name "*.ts" -o -name "*.css" | grep -v node_modules | grep -v .next)
    local needs_rebuild=false

    for f in $changed_files; do
        if [ -f "$f" ]; then
            echo "  Changed: ${f#$PROJECT_DIR/}"
            needs_rebuild=true
        fi
    done

    if [ "$needs_rebuild" = true ]; then
        echo "Rebuilding frontend image (Next.js dev mode — no npm build step)..."
        docker build -t 32_llm_wiki_clean_arch-frontend:latest "$PROJECT_DIR/frontend" 2>&1 | tail -3
        echo "Loading image into kind cluster..."
        kind load docker-image 32_llm_wiki_clean_arch-frontend:latest --name llm-wiki

        echo "Restarting frontend pod..."
        kubectl delete pod -n "$NAMESPACE" -l app=frontend

        echo "Waiting for new pod..."
        sleep 5
        kubectl wait --for=condition=Ready pod -l app=frontend -n "$NAMESPACE" --timeout=60s 2>/dev/null || true
        echo "Frontend synced."
    else
        echo "No frontend changes detected."
    fi
}

run_tests() {
    cd "$PROJECT_DIR"

    if ! curl -s --max-time 2 http://localhost:8000/api/health > /dev/null 2>&1; then
        echo "Setting up port-forward..."
        pkill -f "port-forward.*svc/$APP" 2>/dev/null || true
        nohup kubectl port-forward -n "$NAMESPACE" "svc/$APP" 8000:8000 > /tmp/pf-backend.log 2>&1 &
        sleep 3
    fi

    echo "Running tests..."
    API_BASE_URL="http://localhost:8000" python -m pytest tests/test_all_apis.py -v --tb=short "$@"
}

# ── Main ────────────────────────────────────────────────────────

MODE="full"
EXTRA_ARGS=()

for arg in "$@"; do
    case "$arg" in
        --backend) MODE="backend" ;;
        --frontend) MODE="frontend" ;;
        --test-only) MODE="test" ;;
        *) EXTRA_ARGS+=("$arg") ;;
    esac
done

case "$MODE" in
    backend)
        sync_backend
        ;;
    frontend)
        sync_frontend
        ;;
    test)
        run_tests "${EXTRA_ARGS[@]}"
        ;;
    full)
        sync_backend
        sync_frontend
        run_tests "${EXTRA_ARGS[@]}"
        ;;
esac
