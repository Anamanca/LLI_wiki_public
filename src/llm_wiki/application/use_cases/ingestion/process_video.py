import asyncio
import logging
from datetime import datetime
from llm_wiki.shared.datetime_utils import now
from typing import Callable

from llm_wiki.application.ports.repositories.source_repository import SourceItemRepository
from llm_wiki.domain.entities.source import SourceItem
from llm_wiki.domain.entities.ingestion import IngestionLog
from llm_wiki.domain.exceptions import IngestionFailedError

logger = logging.getLogger(__name__)


class RetryableIngestion:
    def __init__(
        self,
        source_item_repo: SourceItemRepository,
    ):
        self._repo = source_item_repo

    async def execute(
        self,
        item: SourceItem,
        operation: Callable,
        max_retries: int = 3,
        retry_status: str = "classified",
    ) -> SourceItem:
        for attempt in range(max_retries):
            try:
                return await operation(item)
            except asyncio.TimeoutError:
                item.retry_count = attempt + 1
                if attempt < max_retries - 1:
                    item.status = retry_status
                    item.error_message = "timeout"
                    item.retry_after = now()
                    await self._repo.save(item)
                else:
                    item.status = "failed"
                    item.error_message = f"timeout after {max_retries} retries"
                logger.warning("Ingestion timeout for item %s (attempt %d/%d)", item.id.value, attempt + 1, max_retries)
            except Exception as e:
                item.retry_count = attempt + 1
                if attempt < max_retries - 1:
                    item.status = retry_status
                    item.error_message = str(e)
                    await self._repo.save(item)
                else:
                    item.status = "failed"
                    item.error_message = str(e)
                logger.error("Ingestion error for item %s: %s", item.id.value, str(e))
                raise IngestionFailedError(
                    str(item.id.value),
                    reason=str(e),
                    retryable=attempt < max_retries - 1,
                )
        return item


class ProcessVideoUseCase:
    def __init__(
        self,
        source_item_repo: SourceItemRepository,
        retry_handler: RetryableIngestion,
        wiki_integrator,
        embedder,
        llm,
    ):
        self._source_item_repo = source_item_repo
        self._retry = retry_handler
        self._wiki_integrator = wiki_integrator
        self._embedder = embedder
        self._llm = llm

    async def execute(self, item: SourceItem) -> SourceItem:
        item.status = "processing"
        item.started_at = now()
        await self._source_item_repo.save(item)

        async def _process(item: SourceItem) -> SourceItem:
            transcript = item.transcript_text
            if not transcript:
                raise IngestionFailedError(
                    str(item.id.value), reason="no transcript", retryable=False
                )

            page_title = item.title or f"Video {item.external_id[:16]}"

            page = await self._wiki_integrator.execute(
                page_title=page_title,
                content_markdown=transcript,
                source_item=item,
                source_id=item.source_id,
            )

            item.status = "completed"
            item.error_message = None
            await self._source_item_repo.save(item)
            return item

        return await self._retry.execute(item, _process)
