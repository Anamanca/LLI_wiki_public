#!/usr/bin/env bash
#
# Run API integration tests against the deployed backend.
#
# Usage:
#   ./scripts/test-apis.sh                          # auto-detect backend URL
#   ./scripts/test-apis.sh http://localhost:8000    # explicit backend URL
#   ./scripts/test-apis.sh --critical-only          # only frontend-breaking bug tests
#   ./scripts/test-apis.sh --report                 # run all + generate JSON report
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# ── Determine target URL ──────────────────────────────────────────

if [[ $# -ge 1 && ! "$1" =~ ^-- ]]; then
    BASE_URL="$1"
    shift
else
    # Auto-detect: try K8s port-forward or local Docker
    if curl -s --max-time 2 http://localhost:8000/api/health > /dev/null 2>&1; then
        BASE_URL="http://localhost:8000"
        echo "✓ Detected backend at localhost:8000"
    elif curl -s --max-time 2 http://localhost:3000/api/health > /dev/null 2>&1; then
        BASE_URL="http://localhost:3000"
        echo "✓ Detected frontend proxy at localhost:3000 (testing through Next.js rewrites)"
    else
        echo "❌ Cannot auto-detect backend. Provide URL as argument:"
        echo "   $0 http://your-backend:8000"
        exit 1
    fi
fi

echo "  Target URL: $BASE_URL"
echo ""

# ── Install test deps if needed ────────────────────────────────────

cd "$PROJECT_DIR"

if ! python -c "import httpx" 2>/dev/null; then
    echo "Installing httpx..."
    pip install httpx pytest pytest-timeout pytest-json-report -q
fi

# ── Build pytest args ──────────────────────────────────────────────

PYTEST_ARGS="-v --tb=short"

if [[ "${1:-}" == "--critical-only" ]]; then
    PYTEST_ARGS="$PYTEST_ARGS -m critical"
    shift 2>/dev/null || true
elif [[ "${1:-}" == "--report" ]]; then
    PYTEST_ARGS="$PYTEST_ARGS --json-report --json-report-file=test_report.json"
    shift 2>/dev/null || true
fi

# Pass through extra args
PYTEST_ARGS="$PYTEST_ARGS $*"

# ── Run ────────────────────────────────────────────────────────────

echo "Running: API_BASE_URL=$BASE_URL pytest tests/test_all_apis.py $PYTEST_ARGS"
echo ""

API_BASE_URL="$BASE_URL" python -m pytest tests/test_all_apis.py $PYTEST_ARGS

echo ""
echo "Done. Run with --critical-only to focus on frontend-breaking bugs."
