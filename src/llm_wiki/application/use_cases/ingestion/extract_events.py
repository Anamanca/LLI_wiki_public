import logging

from llm_wiki.application.ports.repositories.event_repository import EventRepository
from llm_wiki.application.ports.search.vector_search import LLMClientPort, EmbeddingServicePort
from llm_wiki.application.ports.repositories.page_repository import PageRepository
from llm_wiki.domain.entities.event import EventCanonical, EventObservation
from llm_wiki.domain.entities.entity import Entity, EventEntityLink
from llm_wiki.domain.value_objects.identifiers import EventId, PageId
from llm_wiki.application.ports.repositories.entity_repository import EntityRepository

from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


class ExtractEventsUseCase:
    def __init__(
        self,
        event_repo: EventRepository,
        entity_repo: EntityRepository,
        llm: LLMClientPort,
        embedder: EmbeddingServicePort,
    ):
        self._event_repo = event_repo
        self._entity_repo = entity_repo
        self._llm = llm
        self._embedder = embedder

    async def execute(
        self,
        page_id: PageId,
        page_title: str,
        content_markdown: str,
    ) -> list[EventCanonical]:
        truncated = content_markdown[:8000]
        prompt = (
            f"Extract key events from the following wiki page content. "
            f"Return a JSON array of events. Each event has: title, date (YYYY-MM-DD), "
            f"category, description, entities (list of {{name, type}}). "
            f"Page title: {page_title}\n\nContent:\n{truncated}"
        )

        events: list[EventCanonical] = []

        try:
            response = await self._llm.chat_completion(
                messages=[
                    {"role": "system", "content": "You extract structured events from text. Return only valid JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
            )

            import json
            data = json.loads(response)
            if isinstance(data, dict):
                data = [data]

            for evt in data[:50]:
                event_title = evt.get("title", "Untitled Event")
                existing = await self._event_repo.find_by_title(event_title, limit=1)

                if existing:
                    canonical = existing[0]
                    canonical.observation_count += 1
                else:
                    canonical = EventCanonical(
                        id=EventId(str(uuid4())),
                        title=event_title,
                        normalized_date=datetime.strptime(evt["date"], "%Y-%m-%d").date() if "date" in evt else None,
                        category=evt.get("category"),
                        entities=evt.get("entities", {}),
                        importance_score=0.5,
                        first_seen_at=datetime.utcnow(),
                        observation_count=1,
                    )

                try:
                    emb = await self._embedder.embed(canonical.title)
                    canonical.canonical_embedding = emb.vector
                except Exception:
                    pass

                canonical = await self._event_repo.save(canonical)

                observation = EventObservation(
                    id=EventId(str(uuid4())),
                    event_id=canonical.id,
                    page_id=page_id,
                    observation_type="initial_report",
                    description=evt.get("description", ""),
                    confidence=0.7,
                    extracted_at=datetime.utcnow(),
                )
                await self._event_repo.save_observation(observation)

                for ent in evt.get("entities", [])[:10]:
                    entity = await self._entity_repo.find_by_name_and_type(
                        ent.get("name", ""), ent.get("type", "other")
                    )
                    if not entity:
                        entity = Entity(
                            id=EventId(str(uuid4())),
                            name=ent.get("name", "Unknown"),
                            type=ent.get("type", "other"),
                            first_seen_at=datetime.utcnow(),
                        )
                        entity = await self._entity_repo.save(entity)

                    link = EventEntityLink(
                        id=EventId(str(uuid4())),
                        event_id=canonical.id,
                        entity_id=entity.id,
                        relationship_type="mentions",
                        confidence=0.7,
                        extracted_at=datetime.utcnow(),
                    )
                    await self._entity_repo.save_event_link(link)

                events.append(canonical)

        except Exception as e:
            logger.error("Event extraction failed for page %s: %s", page_title, str(e))

        return events
