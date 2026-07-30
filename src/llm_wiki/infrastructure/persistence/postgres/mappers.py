from llm_wiki.domain.entities.source import Source as DomainSource, SourceItem as DomainSourceItem
from llm_wiki.domain.entities.page import Page as DomainPage, PageSection as DomainPageSection, PageLink as DomainPageLink, PageSnapshot as DomainPageSnapshot
from llm_wiki.domain.entities.event import EventCanonical as DomainEventCanonical, EventObservation as DomainEventObservation, EventTimelineChain as DomainEventTimelineChain
from llm_wiki.domain.entities.entity import Entity as DomainEntity, EventEntityLink as DomainEventEntityLink, EntityRelation as DomainEntityRelation
from llm_wiki.domain.entities.media import MediaAsset as DomainMediaAsset
from llm_wiki.domain.entities.ingestion import IngestionLog as DomainIngestionLog
from llm_wiki.domain.entities.worker import WorkerHeartbeat as DomainWorkerHeartbeat, TelegramSubscriber as DomainTelegramSubscriber, ScanLock as DomainScanLock, ApiKey as DomainApiKey, CronJob as DomainCronJob
from llm_wiki.domain.value_objects.identifiers import SourceId, SourceItemId, PageId, EventId

from llm_wiki.infrastructure.persistence.postgres import models as orm
from uuid import UUID


def _to_uuid_str(val: str | UUID | None) -> str | None:
    return str(val) if val is not None else None


_id_map: dict[type, type] = {
    DomainSource: SourceId,
    DomainSourceItem: SourceItemId,
    DomainPage: PageId,
    DomainPageSection: PageId,
    DomainPageLink: PageId,
    DomainEventCanonical: EventId,
    DomainEventObservation: EventId,
    DomainEventTimelineChain: EventId,
    DomainEntity: EventId,
    DomainEventEntityLink: EventId,
    DomainEntityRelation: EventId,
}


class SourceMapper:
    @staticmethod
    def to_domain(m: orm.Source) -> DomainSource:
        return DomainSource(
            id=SourceId(m.id),
            name=m.name,
            platform=m.platform,
            external_id=m.external_id,
            url=m.url,
            added_at=m.added_at,
            last_checked_at=m.last_checked_at,
            last_video_published_at=m.last_video_published_at,
            status=m.status,
            config=m.config or {},
        )

    @staticmethod
    def to_orm(d: DomainSource, existing: orm.Source | None = None) -> orm.Source:
        target = existing or orm.Source(id=d.id.value)
        target.name = d.name
        target.platform = d.platform
        target.external_id = d.external_id
        target.url = d.url
        target.last_checked_at = d.last_checked_at
        target.last_video_published_at = d.last_video_published_at
        target.status = d.status
        target.config = d.config
        return target


class SourceItemMapper:
    @staticmethod
    def to_domain(m: orm.SourceItem) -> DomainSourceItem:
        return DomainSourceItem(
            id=SourceItemId(m.id),
            source_id=SourceId(m.source_id),
            external_id=m.external_id,
            title=m.title,
            url=m.url,
            published_at=m.published_at,
            status=m.status,
            started_at=m.started_at,
            retry_count=m.retry_count or 0,
            priority=m.priority or 0,
            retry_after=m.retry_after,
            error_message=m.error_message,
            transcript_text=m.transcript_text,
            transcript_json=m.transcript_json,
            created_at=m.created_at,
        )

    @staticmethod
    def to_orm(d: DomainSourceItem, existing: orm.SourceItem | None = None) -> orm.SourceItem:
        target = existing or orm.SourceItem(id=d.id.value)
        target.source_id = d.source_id.value
        target.external_id = d.external_id
        target.title = d.title
        target.url = d.url
        target.published_at = d.published_at
        target.status = d.status
        target.started_at = d.started_at
        target.retry_count = d.retry_count
        target.priority = d.priority
        target.retry_after = d.retry_after
        target.error_message = d.error_message
        target.transcript_text = d.transcript_text
        target.transcript_json = d.transcript_json
        return target


class PageMapper:
    @staticmethod
    def to_domain(m: orm.Page) -> DomainPage:
        return DomainPage(
            id=PageId(m.id),
            source_id=SourceId(m.source_id) if m.source_id else None,
            source_item_id=SourceItemId(m.source_item_id) if m.source_item_id else None,
            title=m.title,
            slug=m.slug,
            content_markdown=m.content_markdown,
            summary=m.summary,
            domain=m.domain,
            key_entities=m.key_entities,
            summary_vector=m.summary_vector,
            created_at=m.created_at,
            updated_at=m.updated_at,
            published_at=m.published_at,
            status=m.status,
        )

    @staticmethod
    def to_orm(d: DomainPage, existing: orm.Page | None = None) -> orm.Page:
        target = existing or orm.Page(id=d.id.value)
        target.source_id = d.source_id.value if d.source_id else None
        target.source_item_id = d.source_item_id.value if d.source_item_id else None
        target.title = d.title
        target.slug = d.slug
        target.content_markdown = d.content_markdown
        target.summary = d.summary
        target.domain = d.domain
        target.key_entities = d.key_entities
        target.summary_vector = d.summary_vector
        target.published_at = d.published_at
        target.status = d.status
        return target


class PageSectionMapper:
    @staticmethod
    def to_domain(m: orm.PageSection, include_fts: bool = False) -> DomainPageSection:
        return DomainPageSection(
            id=PageId(m.id),
            page_id=PageId(m.page_id),
            source_id=SourceId(m.source_id) if m.source_id else None,
            section_order=m.section_order or 0,
            title=m.title,
            content_markdown=m.content_markdown,
            section_vector=m.section_vector,
            fts_vector=m.fts_vector if include_fts else None,
            source_ref=m.source_ref,
            created_at=m.created_at,
        )

    @staticmethod
    def to_orm(d: DomainPageSection, existing: orm.PageSection | None = None) -> orm.PageSection:
        target = existing or orm.PageSection(id=d.id.value)
        target.page_id = d.page_id.value
        target.source_id = d.source_id.value if d.source_id else None
        target.section_order = d.section_order
        target.title = d.title
        target.content_markdown = d.content_markdown
        target.section_vector = d.section_vector
        target.source_ref = d.source_ref
        return target


class EventCanonicalMapper:
    @staticmethod
    def to_domain(m: orm.EventCanonical) -> DomainEventCanonical:
        return DomainEventCanonical(
            id=EventId(m.id),
            title=m.title,
            normalized_date=m.normalized_date,
            normalized_date_end=m.normalized_date_end,
            category=m.category,
            entities=m.entities or {},
            importance_score=m.importance_score or 0.0,
            canonical_embedding=m.canonical_embedding,
            first_seen_at=m.first_seen_at,
            updated_at=m.updated_at,
            observation_count=m.observation_count or 0,
            consensus_summary=m.consensus_summary,
            consensus_generated_at=m.consensus_generated_at,
        )

    @staticmethod
    def to_orm(d: DomainEventCanonical, existing: orm.EventCanonical | None = None) -> orm.EventCanonical:
        target = existing or orm.EventCanonical(id=d.id.value)
        target.title = d.title
        target.normalized_date = d.normalized_date
        target.normalized_date_end = d.normalized_date_end
        target.category = d.category
        target.entities = d.entities
        target.importance_score = d.importance_score
        target.canonical_embedding = d.canonical_embedding
        target.observation_count = d.observation_count
        target.consensus_summary = d.consensus_summary
        target.consensus_generated_at = d.consensus_generated_at
        return target


class EventObservationMapper:
    @staticmethod
    def to_domain(m: orm.EventObservation, include_fts: bool = False) -> DomainEventObservation:
        return DomainEventObservation(
            id=EventId(m.id),
            event_id=EventId(m.event_id),
            source_id=SourceId(m.source_id) if m.source_id else None,
            page_id=PageId(m.page_id) if m.page_id else None,
            source_published_at=m.source_published_at,
            extracted_at=m.extracted_at,
            observation_type=m.observation_type or "initial_report",
            description=m.description,
            impact_direction=m.impact_direction,
            metrics=m.metrics or {},
            confidence=m.confidence or 0.5,
            embedding=m.embedding,
            fts_vector=m.fts_vector if include_fts else None,
            sentiment_score=m.sentiment_score,
            stance=m.stance,
        )

    @staticmethod
    def to_orm(d: DomainEventObservation, existing: orm.EventObservation | None = None) -> orm.EventObservation:
        target = existing or orm.EventObservation(id=d.id.value)
        target.event_id = d.event_id.value
        target.source_id = d.source_id.value if d.source_id else None
        target.page_id = d.page_id.value if d.page_id else None
        target.source_published_at = d.source_published_at
        target.observation_type = d.observation_type
        target.description = d.description
        target.impact_direction = d.impact_direction
        target.metrics = d.metrics
        target.confidence = d.confidence
        target.embedding = d.embedding
        target.sentiment_score = d.sentiment_score
        target.stance = d.stance
        return target


class EventTimelineChainMapper:
    @staticmethod
    def to_domain(m: orm.EventTimelineChain) -> DomainEventTimelineChain:
        return DomainEventTimelineChain(
            id=EventId(m.id),
            from_event_id=EventId(m.from_event_id),
            to_event_id=EventId(m.to_event_id),
            relation_type=m.relation_type,
            description=m.description,
            confidence=m.confidence or 0.5,
            created_at=m.created_at,
        )

    @staticmethod
    def to_orm(d: DomainEventTimelineChain, existing: orm.EventTimelineChain | None = None) -> orm.EventTimelineChain:
        target = existing or orm.EventTimelineChain(id=d.id.value)
        target.from_event_id = d.from_event_id.value
        target.to_event_id = d.to_event_id.value
        target.relation_type = d.relation_type
        target.description = d.description
        target.confidence = d.confidence
        return target


class EntityMapper:
    @staticmethod
    def to_domain(m: orm.Entity) -> DomainEntity:
        return DomainEntity(
            id=EventId(m.id),
            name=m.name,
            type=m.type,
            canonical_name=m.canonical_name,
            ticker=m.ticker,
            metadata=m.extra or {},
            first_seen_at=m.first_seen_at,
        )

    @staticmethod
    def to_orm(d: DomainEntity, existing: orm.Entity | None = None) -> orm.Entity:
        target = existing or orm.Entity(id=d.id.value)
        target.name = d.name
        target.type = d.type
        target.canonical_name = d.canonical_name
        target.ticker = d.ticker
        target.extra = d.metadata
        return target


class EventEntityLinkMapper:
    @staticmethod
    def to_domain(m: orm.EventEntityLink) -> DomainEventEntityLink:
        return DomainEventEntityLink(
            id=EventId(m.id),
            event_id=EventId(m.event_id),
            entity_id=EventId(m.entity_id),
            relationship_type=m.relationship_type,
            confidence=m.confidence or 0.5,
            extracted_at=m.extracted_at,
        )

    @staticmethod
    def to_orm(d: DomainEventEntityLink, existing: orm.EventEntityLink | None = None) -> orm.EventEntityLink:
        target = existing or orm.EventEntityLink(id=d.id.value)
        target.event_id = d.event_id.value
        target.entity_id = d.entity_id.value
        target.relationship_type = d.relationship_type
        target.confidence = d.confidence
        return target


class EntityRelationMapper:
    @staticmethod
    def to_domain(m: orm.EntityRelation) -> DomainEntityRelation:
        return DomainEntityRelation(
            id=EventId(m.id),
            from_entity_id=EventId(m.from_entity_id),
            to_entity_id=EventId(m.to_entity_id),
            predicate=m.predicate,
            properties=m.properties or {},
            confidence=m.confidence or 0.5,
            source_event_id=EventId(m.source_event_id) if m.source_event_id else None,
            extracted_at=m.extracted_at,
        )

    @staticmethod
    def to_orm(d: DomainEntityRelation, existing: orm.EntityRelation | None = None) -> orm.EntityRelation:
        target = existing or orm.EntityRelation(id=d.id.value)
        target.from_entity_id = d.from_entity_id.value
        target.to_entity_id = d.to_entity_id.value
        target.predicate = d.predicate
        target.properties = d.properties
        target.confidence = d.confidence
        target.source_event_id = d.source_event_id.value if d.source_event_id else None
        return target
