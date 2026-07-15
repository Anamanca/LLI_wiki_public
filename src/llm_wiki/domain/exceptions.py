class DomainException(Exception):
    """Base exception for all domain errors."""


class EntityNotFoundError(DomainException):
    def __init__(self, entity_type: str, entity_id: str):
        self.entity_type = entity_type
        self.entity_id = entity_id
        super().__init__(f"{entity_type} with id {entity_id} not found")


class DuplicateEntityError(DomainException):
    pass


class InvalidStatusTransitionError(DomainException):
    pass


class IngestionFailedError(DomainException):
    def __init__(self, source_item_id: str, reason: str, retryable: bool = True):
        self.source_item_id = source_item_id
        self.reason = reason
        self.retryable = retryable
        super().__init__(f"Ingestion failed for {source_item_id}: {reason}")


class ExternalServiceError(DomainException):
    pass


class RateLimitError(DomainException):
    pass


class ValidationError(DomainException):
    pass
