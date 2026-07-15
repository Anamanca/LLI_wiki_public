from enum import StrEnum


class SourceItemStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    PENDING_TRANSCRIBE = "pending_transcribe"
    NO_CAPTIONS = "no_captions"


class PageStatus(StrEnum):
    PUBLISHED = "published"
    DRAFT = "draft"
    ARCHIVED = "archived"


class EventStatus(StrEnum):
    ACTIVE = "active"
    OBSOLETE = "obsolete"
    MERGED = "merged"


class EntityType(StrEnum):
    PERSON = "person"
    ORGANIZATION = "organization"
    LOCATION = "location"
    EVENT = "event"
    CONCEPT = "concept"
    PRODUCT = "product"
    OTHER = "other"
