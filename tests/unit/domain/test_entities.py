from uuid import uuid4

import pytest

from llm_wiki.domain.entities.page import Page
from llm_wiki.domain.entities.source import SourceItem
from llm_wiki.domain.exceptions import EntityNotFoundError
from llm_wiki.domain.value_objects.embedding import Embedding, SearchResult
from llm_wiki.domain.value_objects.identifiers import PageId, SourceId, SourceItemId
from llm_wiki.domain.value_objects.status import PageStatus, SourceItemStatus


class TestSourceItem:
    def test_create_source_item(self):
        item = SourceItem(
            id=SourceItemId(uuid4()),
            source_id=SourceId(uuid4()),
            external_id="dQw4w9WgXcQ",
            title="Test Video",
            status=SourceItemStatus.PENDING.value,
        )
        assert item.status == "pending"
        assert item.retry_count == 0
        assert item.priority == 0

    def test_status_default(self):
        item = SourceItem(
            id=SourceItemId(uuid4()),
            source_id=SourceId(uuid4()),
            external_id="test",
        )
        assert item.status == "pending"


class TestPage:
    def test_create_page(self):
        page = Page(
            id=PageId(uuid4()),
            title="Test Page",
            slug="test-page",
            status=PageStatus.PUBLISHED.value,
        )
        assert page.status == "published"
        assert page.slug == "test-page"


class TestEmbeddingValueObject:
    def test_valid_embedding(self):
        vec = [0.1] * 1024
        emb = Embedding(vector=vec)
        assert emb.dimensions == 1024

    def test_invalid_dimension_raises(self):
        with pytest.raises(ValueError):
            Embedding(vector=[0.1] * 512)


class TestSearchResult:
    def test_create_search_result(self):
        result = SearchResult(
            content_id="abc",
            content_type="page_section",
            title="Test",
            content="content here",
            score=0.95,
            metadata={"page_title": "Test Page"},
        )
        assert result.score == 0.95


class TestDomainExceptions:
    def test_entity_not_found(self):
        exc = EntityNotFoundError("Page", "123")
        assert "Page" in str(exc)
        assert "123" in str(exc)
        assert exc.entity_type == "Page"
        assert exc.entity_id == "123"

    def test_ingestion_failed(self):
        from llm_wiki.domain.exceptions import IngestionFailedError

        exc = IngestionFailedError("item-1", "timeout", retryable=True)
        assert exc.retryable is True
        assert "item-1" in str(exc)
