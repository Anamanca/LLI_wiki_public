# ============================================================================
# LLM Wiki — Quality Gates (run locally before pushing)
#
#   make lint       ruff format + ruff check + mypy (same as CI lint job)
#   make test       pytest with coverage (needs running postgres + redis)
#   make security   bandit + safety + Docker build (advisory, non-blocking)
#   make ci         all of the above
#
# ============================================================================

PYTHONPATH := src
VENV_PYTHON := $(shell which python3 2>/dev/null || which python 2>/dev/null)

# ── Lint ────────────────────────────────────────────────────────────────────

.PHONY: lint
lint: ruff-format ruff-check mypy
	@echo "✅ All lint checks passed"

.PHONY: ruff-format
ruff-format:
	@echo "→ Ruff format (strict)"
	ruff format --check .

.PHONY: ruff-check
ruff-check:
	@echo "→ Ruff lint"
	ruff check .

.PHONY: mypy
mypy:
	@echo "→ Mypy type check (advisory)"
	-PYTHONPATH=$(PYTHONPATH) mypy src/llm_wiki

.PHONY: ruff-fix
ruff-fix:
	ruff format .
	ruff check --fix .

# ── Test ────────────────────────────────────────────────────────────────────

.PHONY: test
test:
	@echo "→ Running tests with coverage"
	PYTHONPATH=$(PYTHONPATH) pytest --cov=llm_wiki --cov-report=xml --cov-report=term -m "not slow"

.PHONY: test-quick
test-quick:
	@echo "→ Running tests (no coverage)"
	PYTHONPATH=$(PYTHONPATH) pytest -x -m "not slow"

# ── Security (advisory) ─────────────────────────────────────────────────────

.PHONY: security
security: bandit safety-check docker-build
	@echo "✅ All security checks completed (advisory)"

.PHONY: bandit
bandit:
	@echo "→ Bandit SAST scan"
	bandit -r src/ --ini .bandit --severity-level medium

.PHONY: safety-check
safety-check:
	@echo "→ Safety CVE scan"
	safety check --full-report

.PHONY: docker-build
docker-build:
	@echo "→ Docker build (backend)"
	docker build -t llm-wiki-backend:ci-test .
	@echo "→ Docker build (frontend)"
	docker build --network=host -t llm-wiki-frontend:ci-test -f frontend/Dockerfile frontend/

# ── Full CI ─────────────────────────────────────────────────────────────────

.PHONY: ci
ci: lint test security
	@echo "🎉 All CI checks completed"

# ── Help ────────────────────────────────────────────────────────────────────

.PHONY: help
help:
	@echo "Usage:"
	@echo "  make lint       — ruff format + ruff check + mypy"
	@echo "  make test       — pytest with coverage"
	@echo "  make test-quick — pytest fast (no coverage, stop on first fail)"
	@echo "  make security   — bandit + safety + Docker build"
	@echo "  make ci         — all quality gates"
	@echo "  make ruff-fix   — auto-format + auto-fix lint errors"
