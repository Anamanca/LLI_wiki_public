"""
Comprehensive API integration tests for LLM Wiki backend.

This test suite validates EVERY API endpoint against the shapes the frontend expects.
Each test checks: HTTP status, response structure, required fields, and data types.

CRITICAL BUGS THIS DETECTS:
  1. GET /api/sources returns flat array [] but frontend expects {sources: [...], total: N}
  2. GET /api/pages missing "page" and "per_page" fields that frontend expects
  3. GET /api/search returns {results: [{id,title,content,score}]} but frontend expects
     {results: [{id,title,slug,summary,source_name,published_at}], total: N}
  4. POST /api/query returns {answer,sources,pipeline_steps,cache_hit} but frontend expects
     {answer,citations,sources_used,tokens_used,latency_ms}
  5. POST /api/query/stream sends {type:"chunk"} but frontend expects {type:"token"}
     sends {type:"sources"}+{type:"done"} but frontend expects {type:"complete"}
  6. GET /api/health missing "db" field that frontend expects
  7. GET /api/pages/{slug} from pages.py (registered first) lacks sections/media_assets/
     linked_pages/source_name that frontend PageDetail expects
  8. ENABLE_STUB_ROUTES not set → all admin dashboard routes return 404

Usage:
    # Test against local dev server
    pytest tests/test_all_apis.py -v --base-url http://localhost:8000

    # Test against deployed K8s backend (through Next.js proxy)
    pytest tests/test_all_apis.py -v --base-url https://your-domain.com

    # Test with specific markers
    pytest tests/test_all_apis.py -v -m "critical" --base-url http://localhost:8000

    # Report-only mode: don't fail, just print the report
    pytest tests/test_all_apis.py -v --base-url http://localhost:8000 --tb=short

    # Run and save JSON report
    pytest tests/test_all_apis.py -v --base-url http://localhost:8000 --json-report
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import pytest
import pytest_asyncio
import httpx

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_PREFIX = "/api"


@dataclass
class TestResult:
    endpoint: str
    method: str
    status_code: int
    passed: bool
    error: str = ""
    mismatch_fields: list[str] = field(default_factory=list)


def api(path: str) -> str:
    """Build full API URL."""
    return f"{BASE_URL}{API_PREFIX}{path}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class ApiTester:
    """Wraps httpx.AsyncClient with response validation helpers."""

    def __init__(self, client: httpx.AsyncClient):
        self.client = client

    async def get(self, path: str, expected_status: int = 200, **kwargs) -> httpx.Response:
        url = api(path)
        r = await self.client.get(url, **kwargs)
        assert r.status_code == expected_status, f"GET {path} → {r.status_code}, body: {r.text[:500]}"
        return r

    async def post(self, path: str, json_body: dict = None, expected_status: int = 200, **kwargs) -> httpx.Response:
        url = api(path)
        r = await self.client.post(url, json=json_body or {}, **kwargs)
        assert r.status_code == expected_status, f"POST {path} → {r.status_code}, body: {r.text[:500]}"
        return r

    async def patch(self, path: str, json_body: dict = None, expected_status: int = 200, **kwargs) -> httpx.Response:
        url = api(path)
        r = await self.client.patch(url, json=json_body or {}, **kwargs)
        assert r.status_code == expected_status, f"PATCH {path} → {r.status_code}, body: {r.text[:500]}"
        return r

    async def delete(self, path: str, expected_status: int = 200, **kwargs) -> httpx.Response:
        url = api(path)
        r = await self.client.delete(url, **kwargs)
        assert r.status_code == expected_status, f"DELETE {path} → {r.status_code}, body: {r.text[:500]}"
        return r

    async def put(self, path: str, json_body: dict = None, expected_status: int = 200, **kwargs) -> httpx.Response:
        url = api(path)
        r = await self.client.put(url, json=json_body or {}, **kwargs)
        assert r.status_code == expected_status, f"PUT {path} → {r.status_code}, body: {r.text[:500]}"
        return r

    def check_fields(self, data: Any, required: list[str], path: str, allow_empty: bool = True):
        """Assert `data` (dict or list[dict]) has all `required` keys."""
        items = data if isinstance(data, list) else [data]
        for item in items:
            if not isinstance(item, dict):
                raise AssertionError(f"{path}: expected dict, got {type(item).__name__}: {item}")
            for field in required:
                assert field in item, f"{path}: missing required field '{field}'. Got keys: {list(item.keys())}"

    def check_field_types(self, data: dict, field_types: dict, path: str):
        """Assert fields have expected Python types."""
        for field, expected_type in field_types.items():
            if field not in data:
                continue
            actual = data[field]
            if actual is None:
                continue
            assert isinstance(actual, expected_type), (
                f"{path}: field '{field}' expected {expected_type.__name__}, got {type(actual).__name__}: {actual}"
            )


# ---------------------------------------------------------------------------
# Fixtures — function-scoped to avoid event-loop issues across tests
# ---------------------------------------------------------------------------

@pytest_asyncio.fixture
async def client():
    """Function-scoped async HTTP client."""
    async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as c:
        yield c


@pytest_asyncio.fixture
async def api_tester(client):
    yield ApiTester(client)


# ===================================================================
# 1. HEALTH CHECK
# ===================================================================

class TestHealth:
    """GET /api/health — Frontend calls fetchHealth() expecting {status, db}."""

    async def test_health_ok(self, api_tester: ApiTester):
        r = await api_tester.get("/health")
        data = r.json()
        assert "status" in data
        assert data["status"] == "ok"

    async def test_health_frontend_shape(self, api_tester: ApiTester):
        """BUG: Frontend expects {status: string, db: string} but backend returns {status, version}."""
        r = await api_tester.get("/health")
        data = r.json()
        missing = [f for f in ["db"] if f not in data]
        if missing:
            pytest.fail(
                f"FRONTEND MISMATCH: fetchHealth() expects 'db' field in response. "
                f"Backend returned: {data}. Missing fields: {missing}. "
                f"Fix: add 'db' field or update frontend type."
            )


# ===================================================================
# 2. QUERY (RAG Pipeline)
# ===================================================================

class TestQuery:
    """POST /api/query and POST /api/query/stream."""

    async def test_query_non_streaming(self, api_tester: ApiTester):
        """Basic RAG query — tests cache, embed, vector search, keyword search, LLM."""
        r = await api_tester.post("/query", {
            "question": "What is this project?",
            "top_k": 3,
        })
        data = r.json()
        # Check required fields exist (backend returns frontend-compatible shape now)
        for field in ["answer", "citations", "sources_used", "tokens_used", "latency_ms"]:
            assert field in data, f"Missing field '{field}' in query response"

    async def test_query_frontend_shape(self, api_tester: ApiTester):
        """Check POST /api/query returns frontend-compatible shape."""
        r = await api_tester.post("/query", {
            "question": "test question",
            "top_k": 3,
        })
        data = r.json()

        frontend_required = ["answer", "citations", "sources_used", "tokens_used", "latency_ms"]
        missing = [f for f in frontend_required if f not in data]
        if missing:
            pytest.fail(f"FRONTEND MISMATCH: POST /api/query missing fields: {missing}\n  Got: {list(data.keys())}")

    async def test_query_with_source_filter(self, api_tester: ApiTester):
        """Query filtered by source_id."""
        r = await api_tester.post("/query", {
            "question": "test",
            "source_id": "00000000-0000-0000-0000-000000000000",
            "top_k": 5,
        })
        data = r.json()
        assert "answer" in data

    async def test_query_streaming(self, api_tester: ApiTester):
        """POST /api/query/stream — SSE streaming."""
        r = await api_tester.post("/query/stream", {
            "question": "test",
            "top_k": 3,
        })
        assert "text/event-stream" in r.headers.get("content-type", "")
        body = r.text
        assert "data:" in body, f"No SSE data in stream: {body[:300]}"

    async def test_query_stream_frontend_shape(self, api_tester: ApiTester):
        """BUG: Backend sends {type:"chunk"}, {type:"sources"}, {type:"done"}
        but frontend expects {type:"token"}, {type:"complete"}."""
        r = await api_tester.post("/query/stream", {
            "question": "test",
            "top_k": 3,
        })
        body = r.text
        lines = [l for l in body.split("\n") if l.startswith("data: ") and l != "data: [DONE]"]

        type_values = set()
        for line in lines:
            try:
                payload = json.loads(line[6:])
                if isinstance(payload, dict) and "type" in payload:
                    type_values.add(payload["type"])
            except (json.JSONDecodeError, KeyError):
                pass

        frontend_types = {"token", "complete"}
        backend_types = {"metadata", "chunk", "sources", "done"}

        if backend_types & type_values and not (frontend_types & type_values):
            pytest.fail(
                f"FRONTEND MISMATCH: Stream event types don't match.\n"
                f"  Backend sends: {sorted(backend_types & type_values)}\n"
                f"  Frontend expects: {sorted(frontend_types)}\n"
                f"  Found in stream: {sorted(type_values)}\n"
                f"  Fix: useQueryStream.ts line 83 expects type==='token' but backend sends type==='chunk'"
            )


# ===================================================================
# 3. SOURCES
# ===================================================================

class TestSources:
    """Source CRUD endpoints."""

    @pytest.fixture
    async def created_source_id(self, api_tester: ApiTester):
        """Create a test source and return its ID, clean up after."""
        r = await api_tester.post("/sources", {
            "name": f"test-source-{uuid.uuid4().hex[:8]}",
            "platform": "youtube",
            "external_id": f"UC-test-{uuid.uuid4().hex[:8]}",
            "url": f"https://youtube.com/@test{uuid.uuid4().hex[:6]}",
        })
        data = r.json()
        src_id = data["id"]
        yield src_id
        # Cleanup: soft-delete via DELETE /api/sources/{id}
        try:
            async with httpx.AsyncClient(timeout=10) as c:
                await c.delete(api(f"/sources/{src_id}"))
        except Exception:
            pass

    async def test_create_source(self, api_tester: ApiTester):
        r = await api_tester.post("/sources", {
            "name": "test create source",
            "platform": "youtube",
            "external_id": f"UC-{uuid.uuid4().hex[:8]}",
            "url": "https://youtube.com/@test",
        })
        data = r.json()
        assert data["name"] == "test create source"
        assert data["platform"] == "youtube"
        assert "id" in data

        # Cleanup
        async with httpx.AsyncClient(timeout=10) as c:
            await c.delete(api(f"/sources/{data['id']}"))

    async def test_list_sources_response_shape(self, api_tester: ApiTester):
        """BUG: Backend returns flat array [] but frontend expects {sources: [...], total: N}."""
        r = await api_tester.get("/sources")
        data = r.json()

        if isinstance(data, list):
            pytest.fail(
                "CRITICAL BUG: GET /api/sources returns a flat ARRAY but frontend expects "
                "{sources: [...], total: N}.\n"
                "  Frontend code: fetchSources() calls request<SourceListResponse>('/sources')\n"
                "  SourceListResponse = {sources: Source[], total: number}\n"
                "  This causes: Cannot read properties of undefined (reading 'sources') or "
                "frontend renders empty because it tries data.sources but gets an array.\n"
                "  Fix: In sources.py list_sources(), wrap result in {\"sources\": [...], \"total\": len}"
            )

        if isinstance(data, dict):
            if "sources" not in data:
                pytest.fail(
                    "FRONTEND MISMATCH: GET /api/sources missing 'sources' key. "
                    f"Got keys: {list(data.keys())}"
                )
            if "total" not in data:
                pytest.fail(
                    "FRONTEND MISMATCH: GET /api/sources missing 'total' key. "
                    f"Got keys: {list(data.keys())}"
                )

    async def test_create_source_frontend_shape(self, api_tester: ApiTester):
        """BUG: Backend SourceResponse lacks 'config' field that frontend Source type expects."""
        r = await api_tester.post("/sources", {
            "name": "shape test",
            "platform": "youtube",
            "external_id": f"UC-{uuid.uuid4().hex[:8]}",
            "url": "https://youtube.com/@test",
        })
        data = r.json()

        # Frontend Source type requires: id, name, platform, external_id, url, added_at,
        # last_checked_at, status, config
        frontend_required = ["id", "name", "platform", "external_id", "url", "status", "config"]
        missing = [f for f in frontend_required if f not in data]
        if missing:
            pytest.fail(
                f"FRONTEND MISMATCH: POST /api/sources missing fields for frontend Source type.\n"
                f"  Missing: {missing}\n"
                f"  Frontend Source interface requires 'config: Record<string, unknown>'\n"
                f"  Backend returned: {list(data.keys())}"
            )

        # Cleanup
        async with httpx.AsyncClient(timeout=10) as c:
            await c.delete(api(f"/sources/{data['id']}"))

    async def test_get_source_detail(self, api_tester: ApiTester, created_source_id):
        """GET /api/sources/{id} — stub route, checks frontend shape."""
        r = await api_tester.get(f"/sources/{created_source_id}")
        data = r.json()
        frontend_required = [
            "id", "name", "platform", "external_id", "url", "status",
            "video_count", "page_count", "status_breakdown",
        ]
        missing = [f for f in frontend_required if f not in data]
        if missing:
            pytest.fail(
                f"FRONTEND MISMATCH: SourceDetail type needs fields: {missing}\n"
                f"  Backend returned: {list(data.keys())}"
            )

    async def test_patch_source(self, api_tester: ApiTester, created_source_id):
        """PATCH /api/sources/{id}."""
        r = await api_tester.patch(f"/sources/{created_source_id}", {"name": "updated-name"})
        data = r.json()
        # Note: current stubs.py implementation ignores the body — this is a known gap
        assert "id" in data

    async def test_delete_source(self, api_tester: ApiTester):
        """DELETE /api/sources/{id} — soft delete."""
        # Create then delete
        r_create = await api_tester.post("/sources", {
            "name": "to be deleted",
            "platform": "youtube",
            "external_id": f"UC-del-{uuid.uuid4().hex[:8]}",
            "url": "https://youtube.com/@todelete",
        })
        src_id = r_create.json()["id"]

        r = await api_tester.delete(f"/sources/{src_id}")
        data = r.json()
        assert data.get("status") == "deleted"

    async def test_scan_source(self, api_tester: ApiTester, created_source_id):
        """POST /api/sources/{id}/scan — currently a stub."""
        r = await api_tester.post(f"/sources/{created_source_id}/scan")
        data = r.json()
        assert "status" in data
        # Frontend expects: {status, message, new_items_found, restarted_rate_limited, restarted_failed}
        for field in ["message", "new_items_found"]:
            assert field in data, f"Missing '{field}' in scan response"


# ===================================================================
# 4. SOURCE ITEMS
# ===================================================================

class TestSourceItems:
    @pytest.fixture
    async def source_with_item(self, api_tester: ApiTester):
        src_r = await api_tester.post("/sources", {
            "name": f"src-items-{uuid.uuid4().hex[:6]}",
            "platform": "youtube",
            "external_id": f"UC-items-{uuid.uuid4().hex[:8]}",
            "url": "https://youtube.com/@items",
        })
        src_id = src_r.json()["id"]
        yield src_id
        async with httpx.AsyncClient(timeout=10) as c:
            await c.delete(api(f"/sources/{src_id}"))

    async def test_list_source_items(self, api_tester: ApiTester, source_with_item):
        r = await api_tester.get(f"/sources/{source_with_item}/items")
        data = r.json()
        assert "items" in data
        assert "total" in data
        assert isinstance(data["items"], list)

    async def test_list_source_items_filtered(self, api_tester: ApiTester, source_with_item):
        r = await api_tester.get(f"/sources/{source_with_item}/items?status=failed,pending")
        data = r.json()
        assert isinstance(data["items"], list)

    async def test_skip_item_404(self, api_tester: ApiTester):
        """Skip non-existent item → 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await api_tester.post(f"/sources/items/{fake_id}/skip", expected_status=404)

    async def test_retry_item_404(self, api_tester: ApiTester):
        """Retry non-existent item → 404."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await api_tester.post(f"/sources/items/{fake_id}/retry", expected_status=404)

    async def test_transcript_stub(self, api_tester: ApiTester):
        """POST /api/sources/items/{id}/transcript — stub acknowledges."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await api_tester.post(f"/sources/items/{fake_id}/transcript", expected_status=404)


# ===================================================================
# 5. PAGES
# ===================================================================

class TestPages:
    async def test_list_pages_response_shape(self, api_tester: ApiTester):
        """BUG: Backend returns {items, total} but frontend expects {items, total, page, per_page}."""
        r = await api_tester.get("/pages")
        data = r.json()
        assert "items" in data
        assert "total" in data

        missing = [f for f in ["page", "per_page"] if f not in data]
        if missing:
            pytest.fail(
                f"FRONTEND MISMATCH: GET /api/pages missing fields: {missing}\n"
                f"  Frontend PageListResponse requires: items, total, page, per_page\n"
                f"  Backend returned: {list(data.keys())}\n"
                f"  Fix: add 'page' and 'per_page' to pages.py list_pages() response"
            )

    async def test_list_pages_with_source_filter(self, api_tester: ApiTester):
        r = await api_tester.get("/pages?source_id=00000000-0000-0000-0000-000000000000")
        data = r.json()
        assert "items" in data

    async def test_get_page_by_slug_404(self, api_tester: ApiTester):
        await api_tester.get("/pages/nonexistent-slug-12345", expected_status=404)

    async def test_get_page_frontend_shape(self, api_tester: ApiTester):
        """BUG: pages.py GET /pages/{slug} lacks sections/media_assets/linked_pages/source_*
        that frontend PageDetail type expects. The richer version is in stubs.py but gets shadowed."""
        # Create a source first, then a page, then test
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as c:
            # Create source
            src_r = await c.post(api("/sources"), json={
                "name": "page-shape-test",
                "platform": "youtube",
                "external_id": f"UC-pg-{uuid.uuid4().hex[:8]}",
                "url": "https://youtube.com/@pagetest",
            })
            src_id = src_r.json()["id"]

            # Check what the existing pages endpoint returns for any known page
            list_r = await c.get(api("/pages"))
            pages_data = list_r.json()
            items = pages_data.get("items", [])

            if items:
                slug = items[0]["slug"]
                detail_r = await c.get(api(f"/pages/{slug}"))
                detail_data = detail_r.json()

                # Frontend PageDetail needs: sections, media_assets, linked_pages, source_name,
                # source_url, source_video_url
                frontend_fields = ["sections", "media_assets", "linked_pages", "source_name", "source_url"]
                missing = [f for f in frontend_fields if f not in detail_data]
                if missing:
                    pytest.fail(
                        f"FRONTEND MISMATCH: GET /api/pages/{{slug}} (from pages.py, registered first)\n"
                        f"  Missing fields for PageDetail type: {missing}\n"
                        f"  Backend returned: {list(detail_data.keys())}\n"
                        f"  NOTE: stubs.py has a richer version with sections/media_assets/linked_pages\n"
                        f"  but it's shadowed by pages.py which registers first in main.py.\n"
                        f"  Fix: Either swap router registration order, merge into one, or ensure\n"
                        f"  ENABLE_STUB_ROUTES version has different paths."
                    )

            # Cleanup
            await c.delete(api(f"/sources/{src_id}"))


# ===================================================================
# 6. SEARCH
# ===================================================================

class TestSearch:
    async def test_search(self, api_tester: ApiTester):
        r = await api_tester.get("/search?q=test")
        data = r.json()
        assert "results" in data

    async def test_search_frontend_shape(self, api_tester: ApiTester):
        """BUG: Backend returns {results: [{id, title, content, score}]}
        but frontend expects {results: [{id, title, slug, summary, source_name, published_at}], total}."""
        r = await api_tester.get("/search?q=test")
        data = r.json()

        missing_top = [f for f in ["total"] if f not in data]
        if missing_top:
            pytest.fail(
                f"FRONTEND MISMATCH: GET /api/search missing top-level field: {missing_top}\n"
                f"  Frontend SearchResponse = {{results: SearchResult[], total: number}}"
            )

        results = data.get("results", [])
        if results:
            item = results[0]
            backend_fields = {"id", "title", "content", "score"}
            frontend_fields = {"id", "title", "slug", "summary", "source_name", "published_at"}

            has_backend_shape = backend_fields.issubset(set(item.keys()))
            has_frontend_shape = frontend_fields.issubset(set(item.keys()))

            if has_backend_shape and not has_frontend_shape:
                pytest.fail(
                    f"CRITICAL BUG: GET /api/search result items don't match frontend SearchResult type.\n"
                    f"  Backend sends: {sorted(backend_fields)}\n"
                    f"  Frontend expects: {sorted(frontend_fields)}\n"
                    f"  Actual: {sorted(item.keys())}\n"
                    f"  The frontend renders search results using 'slug', 'summary', 'source_name',\n"
                    f"  'published_at' — but backend sends 'content' and 'score' instead.\n"
                    f"  This means the search UI displays nothing or crashes."
                )

    async def test_search_empty_query(self, api_tester: ApiTester):
        """Search with no query param → 422 validation error."""
        r = await api_tester.get("/search", expected_status=422)


# ===================================================================
# 7. PROGRESS & SYSTEM STATS
# ===================================================================

class TestProgress:
    async def test_progress(self, api_tester: ApiTester):
        """GET /api/progress — stub routes only if ENABLE_STUB_ROUTES=true."""
        r = await api_tester.get("/progress")
        data = r.json()
        for field in ["global", "per_source", "alerts", "processing_items"]:
            assert field in data, f"Missing '{field}' in progress response"

    async def test_progress_frontend_shape(self, api_tester: ApiTester):
        """Check frontend Progress type shape."""
        r = await api_tester.get("/progress")
        data = r.json()

        if "global" in data:
            global_required = [
                "pending", "pending_transcribe", "waiting_for_wiki", "processing",
                "done_today", "failed", "rate_limited", "requires_membership",
            ]
            missing = [f for f in global_required if f not in data["global"]]
            if missing:
                pytest.fail(
                    f"FRONTEND MISMATCH: /api/progress 'global' missing fields: {missing}"
                )

    async def test_system_stats(self, api_tester: ApiTester):
        r = await api_tester.get("/system-stats")
        data = r.json()
        for field in ["cpu_percent", "ram_used_gb", "ram_total_gb", "disk_used_gb", "disk_total_gb"]:
            assert field in data, f"Missing '{field}' in system-stats"


# ===================================================================
# 8. GRAPH ENDPOINTS
# ===================================================================

class TestGraph:
    async def test_page_graph(self, api_tester: ApiTester):
        r = await api_tester.get("/graph")
        data = r.json()
        assert "nodes" in data
        assert "edges" in data

    async def test_page_graph_frontend_shape(self, api_tester: ApiTester):
        r = await api_tester.get("/graph")
        data = r.json()
        if data.get("nodes"):
            node = data["nodes"][0]
            for field in ["id", "title", "source_name"]:
                assert field in node, f"GraphNode missing '{field}'"

    async def test_entity_graph(self, api_tester: ApiTester):
        r = await api_tester.get("/entity-graph")
        data = r.json()
        assert "nodes" in data
        assert "edges" in data

    async def test_entity_graph_with_filters(self, api_tester: ApiTester):
        r = await api_tester.get("/entity-graph?entity_type=person&limit=50")
        data = r.json()
        assert isinstance(data["nodes"], list)

    async def test_entity_graph_full(self, api_tester: ApiTester):
        r = await api_tester.get("/entity-graph?full_graph=true")
        data = r.json()
        assert "nodes" in data

    async def test_entity_graph_frontend_shape(self, api_tester: ApiTester):
        r = await api_tester.get("/entity-graph")
        data = r.json()
        if data.get("nodes"):
            node = data["nodes"][0]
            for field in ["id", "label", "type", "ticker", "event_count"]:
                assert field in node, f"EntityGraphNode missing '{field}'"
        if data.get("edges"):
            edge = data["edges"][0]
            for field in ["source", "target", "edge_type", "predicate"]:
                assert field in edge, f"EntityGraphEdge missing '{field}'"

    async def test_cluster_graph(self, api_tester: ApiTester):
        r = await api_tester.get("/cluster-graph")
        data = r.json()
        assert "clusters" in data
        assert "edges" in data

    async def test_cluster_expand(self, api_tester: ApiTester):
        r = await api_tester.get("/cluster-expand")
        data = r.json()
        assert "nodes" in data


# ===================================================================
# 9. ATTENTION ITEMS
# ===================================================================

class TestAttentionItems:
    async def test_attention_items(self, api_tester: ApiTester):
        r = await api_tester.get("/attention-items")
        data = r.json()
        assert "items" in data
        assert "total" in data

    async def test_attention_items_paginated(self, api_tester: ApiTester):
        r = await api_tester.get("/attention-items?page=1&per_page=10")
        data = r.json()
        assert data.get("page") == 1


# ===================================================================
# 10. WORKERS
# ===================================================================

class TestWorkers:
    async def test_workers(self, api_tester: ApiTester):
        r = await api_tester.get("/workers")
        data = r.json()
        assert "workers" in data

    async def test_workers_frontend_shape(self, api_tester: ApiTester):
        r = await api_tester.get("/workers")
        data = r.json()
        workers = data.get("workers", [])
        if workers:
            w = workers[0]
            required = ["worker_id", "status", "alive", "heartbeat_ago_secs",
                        "current_job_id", "current_stage", "cpu_percent", "error_message"]
            missing = [f for f in required if f not in w]
            if missing:
                pytest.fail(f"FRONTEND MISMATCH: WorkerInfo missing fields: {missing}")


# ===================================================================
# 11. RESTART
# ===================================================================

class TestRestart:
    async def test_restart_item_404(self, api_tester: ApiTester):
        fake_id = "00000000-0000-0000-0000-000000000000"
        await api_tester.post(f"/restart/{fake_id}", expected_status=404)

    async def test_restart_source_ok(self, api_tester: ApiTester):
        """Should succeed (even if 0 items restarted)."""
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await api_tester.post(f"/restart/source/{fake_id}")
        data = r.json()
        assert "status" in data
        # Frontend expects: {status, restarted} OR {status, item_id, restarted}
        assert "restarted" in data


# ===================================================================
# 12. ADMIN API KEYS
# ===================================================================

class TestApiKeys:
    async def test_list_api_keys(self, api_tester: ApiTester):
        r = await api_tester.get("/admin/api-keys")
        data = r.json()
        assert isinstance(data, list)

    async def test_list_api_keys_frontend_shape(self, api_tester: ApiTester):
        r = await api_tester.get("/admin/api-keys")
        data = r.json()
        if data:
            key = data[0]
            required = ["id", "provider", "api_key_masked", "model_name", "status",
                        "priority", "rate_limited_until", "usage_count", "last_used_at",
                        "created_at", "updated_at"]
            missing = [f for f in required if f not in key]
            if missing:
                pytest.fail(f"FRONTEND MISMATCH: ApiKeyRow missing fields: {missing}")

    async def test_create_api_key_501(self, api_tester: ApiTester):
        """POST /api/admin/api-keys returns 501 Not Implemented."""
        r = await api_tester.post("/admin/api-keys", {
            "provider": "opencode",
            "api_key": "sk-test-1234",
            "model_name": "test-model",
        }, expected_status=501)

    async def test_update_api_key_501(self, api_tester: ApiTester):
        fake_id = "00000000-0000-0000-0000-000000000000"
        r = await api_tester.put(f"/admin/api-keys/{fake_id}",
                                 {"status": "active"}, expected_status=501)

    async def test_delete_api_key_404(self, api_tester: ApiTester):
        fake_id = "00000000-0000-0000-0000-000000000000"
        await api_tester.delete(f"/admin/api-keys/{fake_id}", expected_status=404)

    async def test_activate_api_key_404(self, api_tester: ApiTester):
        fake_id = "00000000-0000-0000-0000-000000000000"
        await api_tester.post(f"/admin/api-keys/{fake_id}/activate", expected_status=404)


# ===================================================================
# 13. ADMIN CRON JOBS
# ===================================================================

class TestCronJobs:
    async def test_list_cron_jobs(self, api_tester: ApiTester):
        r = await api_tester.get("/admin/cron-jobs")
        data = r.json()
        assert isinstance(data, list)

    async def test_list_cron_jobs_frontend_shape(self, api_tester: ApiTester):
        r = await api_tester.get("/admin/cron-jobs")
        data = r.json()
        if data:
            job = data[0]
            required = ["job_id", "name", "description", "schedule", "job_type",
                        "managed", "status", "last_run"]
            missing = [f for f in required if f not in job]
            if missing:
                pytest.fail(f"FRONTEND MISMATCH: CronJobStatus missing fields: {missing}")

    async def test_start_cron_job(self, api_tester: ApiTester):
        r = await api_tester.post("/admin/cron-jobs/nonexistent-job/start")
        data = r.json()
        assert data.get("success") is True

    async def test_stop_cron_job(self, api_tester: ApiTester):
        r = await api_tester.post("/admin/cron-jobs/nonexistent-job/stop")
        data = r.json()
        assert data.get("success") is True


# ===================================================================
# 14. ADMIN CLEAR ALERTS
# ===================================================================

class TestAdminClearAlerts:
    async def test_clear_alerts(self, api_tester: ApiTester):
        r = await api_tester.delete("/admin/clear-alerts")
        data = r.json()
        assert data.get("status") == "ok"
        assert "deleted" in data


# ===================================================================
# 15. CHAT SESSIONS
# ===================================================================

class TestChatSessions:
    async def test_list_sessions(self, api_tester: ApiTester):
        r = await api_tester.get("/chat/sessions")
        data = r.json()
        assert isinstance(data, list)

    async def test_create_session_stub(self, api_tester: ApiTester):
        r = await api_tester.post("/chat/sessions")
        data = r.json()
        assert "id" in data

    async def test_get_session_stub(self, api_tester: ApiTester):
        r = await api_tester.get("/chat/sessions/test-1")
        data = r.json()
        assert data["id"] == "test-1"

    async def test_update_session_stub(self, api_tester: ApiTester):
        r = await api_tester.put("/chat/sessions/test-1")
        data = r.json()
        assert "id" in data

    async def test_delete_session_stub(self, api_tester: ApiTester):
        r = await api_tester.delete("/chat/sessions/test-1")
        data = r.json()
        assert data.get("status") == "deleted"


# ===================================================================
# 16. ERROR HANDLING
# ===================================================================

class TestErrorHandling:
    async def test_invalid_json_body(self, api_tester: ApiTester):
        """Invalid JSON → should return 422 not 500."""
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(api("/query"), content="not json", headers={"Content-Type": "application/json"})
            assert r.status_code in [400, 422], f"Expected 400/422, got {r.status_code}"

    async def test_invalid_uuid_format(self, api_tester: ApiTester):
        """Invalid UUID → should return 400."""
        await api_tester.get("/sources/not-a-uuid", expected_status=400)

    async def test_missing_required_fields(self, api_tester: ApiTester):
        """POST /api/sources with missing name → 422."""
        await api_tester.post("/sources", {"platform": "youtube"}, expected_status=422)

    async def test_cors_headers(self, api_tester: ApiTester):
        """CORS headers should allow cross-origin requests."""
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.options(api("/health"), headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            })
            # FastAPI CORS middleware should respond
            assert r.status_code in [200, 204], f"OPTIONS returned {r.status_code}"

    async def test_404_nonexistent_route(self, api_tester: ApiTester):
        """Nonexistent route → 404."""
        await api_tester.get("/nonexistent-endpoint-12345", expected_status=404)


# ===================================================================
# 17. STUB ROUTES AVAILABILITY CHECK
# ===================================================================

class TestStubRoutesAvailability:
    """Test that stub routes are available (controlled by ENABLE_STUB_ROUTES env var).
    If these fail with 404, the frontend admin dashboard WILL NOT WORK."""

    STUB_ROUTES = [
        ("GET", "/progress"),
        ("GET", "/system-stats"),
        ("GET", "/workers"),
        ("GET", "/attention-items"),
        ("GET", "/graph"),
        ("GET", "/entity-graph"),
        ("GET", "/cluster-graph"),
        ("GET", "/cluster-expand"),
        ("GET", "/admin/api-keys"),
        ("GET", "/admin/cron-jobs"),
        ("GET", "/chat/sessions"),
    ]

    @pytest.mark.parametrize("method,path", STUB_ROUTES)
    async def test_stub_route_available(self, api_tester: ApiTester, method, path):
        """Each stub route must NOT return 404 — otherwise frontend is broken."""
        if method == "GET":
            r = await api_tester.get(path)
        if r.status_code == 404:
            pytest.fail(
                f"STUB ROUTE UNAVAILABLE: {method} {path} returned 404.\n"
                f"  The frontend admin dashboard depends on this endpoint.\n"
                f"  Fix: Set ENABLE_STUB_ROUTES=true in the backend environment variables.\n"
                f"  All routes in stubs.py require this env var to be registered."
            )


# ===================================================================
# 18. FRONTEND-BACKEND CONTRACT TEST (Cross-cutting)
# ===================================================================

class TestFrontendBackendContract:
    """End-to-end contract tests that simulate exactly what the frontend does."""

    async def test_frontend_health_flow(self, api_tester: ApiTester):
        """Simulate fetchHealth() on app load."""
        r = await api_tester.get("/health")
        data = r.json()
        # Frontend expects {status, db} — if 'db' missing, frontend shows "disconnected"
        if "db" not in data:
            pytest.fail("fetchHealth() expects 'db' field — frontend health indicator broken")

    async def test_frontend_sources_flow(self, api_tester: ApiTester):
        """Simulate useSources() → fetchSources() on sources page load."""
        r = await api_tester.get("/sources")
        data = r.json()
        if isinstance(data, list):
            pytest.fail(
                "fetchSources() broken: expects {sources: [...], total: N}, got array.\n"
                "  Frontend code: request<SourceListResponse>('/sources')\n"
                "  This causes TypeScript type mismatch at runtime → sources page blank/crash"
            )

    async def test_frontend_pages_flow(self, api_tester: ApiTester):
        """Simulate usePages() → fetchPages() on wiki page load."""
        r = await api_tester.get("/pages")
        data = r.json()
        if "page" not in data or "per_page" not in data:
            pytest.fail("fetchPages() expects 'page' and 'per_page' in response — frontend pagination broken")

    async def test_frontend_search_flow(self, api_tester: ApiTester):
        """Simulate useSearch() → searchPages() on search."""
        r = await api_tester.get("/search?q=test")
        data = r.json()
        results = data.get("results", [])
        if results:
            item = results[0]
            # Frontend SearchResult type: {id, title, slug, summary, source_name, published_at}
            if "slug" not in item and "content" in item:
                pytest.fail(
                    "CRITICAL: search results don't match frontend SearchResult type."
                    "  Backend sends 'content' but frontend expects 'slug' + 'summary'."
                    "  Search results page will show empty/undefined values."
                )

    async def test_frontend_query_flow(self, api_tester: ApiTester):
        """Simulate useQueryMutation() → postQuery() on chat/ask."""
        r = await api_tester.post("/query", {"question": "test", "top_k": 3})
        data = r.json()
        # Frontend QueryResponse: answer, citations, sources_used, tokens_used, latency_ms
        if "citations" not in data and "sources" in data:
            pytest.fail(
                "CRITICAL: POST /api/query response doesn't match frontend QueryResponse type.\n"
                "  Backend sends: answer, sources, pipeline_steps, cache_hit\n"
                "  Frontend expects: answer, citations, sources_used, tokens_used, latency_ms\n"
                "  Chat page will show answer but no citations/sources."
            )

    async def test_frontend_stream_flow(self, api_tester: ApiTester):
        """Simulate useQueryStream() → fetch('/api/query/stream') on chat."""
        r = await api_tester.post("/query/stream", {"question": "test", "top_k": 3})
        body = r.text
        lines = [l for l in body.split("\n") if l.startswith("data: ") and l != "data: [DONE]"]

        has_token = False
        has_complete = False
        has_chunk = False
        has_done = False

        for line in lines[:50]:  # check first 50 events
            try:
                obj = json.loads(line[6:])
                t = obj.get("type", "")
                if t == "token":
                    has_token = True
                if t == "complete":
                    has_complete = True
                if t == "chunk":
                    has_chunk = True
                if t == "done":
                    has_done = True
            except json.JSONDecodeError:
                pass

        if (has_chunk or has_done) and not (has_token or has_complete):
            pytest.fail(
                "CRITICAL: Stream event types don't match what frontend expects.\n"
                "  Backend sends: chunk, sources, done\n"
                "  Frontend expects: token, complete\n"
                "  useQueryStream.ts line 83: if (payload.type === 'token')\n"
                "  useQueryStream.ts line 86: } else if (payload.type === 'complete')\n"
                "  Because 'chunk' !== 'token', the answer text is IGNORED.\n"
                "  Because 'done'/'sources' !== 'complete', citations never load."
                "  The chat streaming UI shows NOTHING."
            )

    async def test_frontend_page_detail_flow(self, api_tester: ApiTester):
        """Check that a page detail has the enriched fields frontend needs.
        This catches the pages.py vs stubs.py route shadowing issue."""
        r = await api_tester.get("/pages")
        data = r.json()
        items = data.get("items", [])
        if not items:
            pytest.skip("No pages exist in database to test detail view")

        slug = items[0]["slug"]
        r = await api_tester.get(f"/pages/{slug}")
        detail = r.json()

        # ENABLE_STUB_ROUTES version (stubs.py) has: sections, media_assets, linked_pages, source_name, source_url
        # Non-stub version (pages.py) has: id, title, slug, content_markdown, summary, domain, key_entities, status, created_at, updated_at
        # Frontend PageDetail expects: sections, media_assets, linked_pages, source_name, source_url, source_video_url

        has_enriched = "sections" in detail

        if not has_enriched:
            # This is the pages.py version (non-stub) — check if it provides enough for frontend
            missing = [f for f in ["source_name", "source_url"] if f not in detail]
            note = (
                f"NOTE: GET /api/pages/{{slug}} serving from pages.py (non-enriched).\n"
                f"  Frontend PageDetail expects: sections, media_assets, linked_pages,\n"
                f"  source_name, source_url, source_video_url.\n"
                f"  If page detail page shows blank sections/media: that's because\n"
                f"  stubs.py richer version is shadowed by pages.py router order."
            )
            if missing:
                pytest.fail(f"{note}\n  Missing fields: {missing}")
            else:
                pytest.skip(f"{note}\n  (But basic fields are present, so page may render partially)")
        else:
            # Has enriched data — verify completeness
            for field in ["sections", "media_assets", "linked_pages", "source_name", "source_url"]:
                assert field in detail, f"PageDetail missing '{field}' even in enriched response"
