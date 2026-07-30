import re
from uuid import uuid4

from llm_wiki.application.ports.repositories.entity_repository import EntityRepository
from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.application.ports.repositories.page_repository import (
    PageRepository,
    PageSectionRepository,
)
from llm_wiki.application.ports.search.vector_search import EmbeddingServicePort
from llm_wiki.domain.entities.page import Page, PageSection
from llm_wiki.domain.entities.source import SourceItem
from llm_wiki.domain.value_objects.identifiers import PageId, SourceId
from llm_wiki.shared.datetime_utils import now


class IntegrateWikiUseCase:
    def __init__(
        self,
        page_repo: PageRepository,
        section_repo: PageSectionRepository,
        event_repo: EventRepository,
        entity_repo: EntityRepository,
        embedder: EmbeddingServicePort,
    ):
        self._page_repo = page_repo
        self._section_repo = section_repo
        self._event_repo = event_repo
        self._entity_repo = entity_repo
        self._embedder = embedder

    def _generate_slug(self, title: str, source_id: str) -> str:
        base = re.sub(r"[^a-z0-9\-]", "", title.lower().replace(" ", "-").replace("_", "-"))
        base = re.sub(r"-+", "-", base).strip("-")
        return f"{base}-{source_id[:8]}"

    async def execute(
        self,
        page_title: str,
        content_markdown: str,
        source_item: SourceItem,
        source_id: SourceId,
        domain: str = "",
        key_entities: list[str] | None = None,
    ) -> Page:
        slug = self._generate_slug(page_title, str(source_id.value))
        existing = await self._page_repo.get_by_slug(slug)

        page_id = PageId(existing.id.value) if existing else PageId(str(uuid4()))

        page = Page(
            id=page_id,
            source_id=source_id,
            source_item_id=source_item.id,
            title=page_title,
            slug=slug,
            content_markdown=content_markdown,
            domain=domain,
            key_entities=key_entities or [],
            status="published",
            updated_at=now(),
        )

        page = await self._page_repo.save(page)

        if existing:
            await self._section_repo.delete_by_page(page.id)

        sections = self._split_sections(content_markdown)
        for i, (section_title, section_content) in enumerate(sections):
            if not section_content.strip():
                continue

            section_embedding = None
            try:
                emb = await self._embedder.embed(section_content[:5000])
                section_embedding = emb.vector
            except Exception:
                pass

            section = PageSection(
                id=PageId(str(uuid4())),
                page_id=page.id,
                source_id=source_id,
                section_order=i + 1,
                title=section_title or None,
                content_markdown=section_content,
                section_vector=section_embedding,
                source_ref=f"source:{source_id.value}",
                created_at=now(),
            )
            await self._section_repo.save(section)

        return page

    def _split_sections(self, content: str) -> list[tuple[str, str]]:
        sections = re.split(r"\n(?=#{1,3}\s)", content)
        result = []
        current_title = ""
        for sec in sections:
            sec = sec.strip()
            if not sec:
                continue
            match = re.match(r"^#{1,3}\s+(.+)", sec)
            if match:
                heading = match.group(1).strip()
                body = sec[match.end() :].strip()
                result.append((heading, body))
            else:
                if result:
                    prev_title, prev_body = result[-1]
                    result[-1] = (prev_title, prev_body + "\n" + sec)
                else:
                    result.append(("", sec))
        return result
