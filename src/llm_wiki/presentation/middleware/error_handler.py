import logging

from fastapi import Request
from fastapi.responses import JSONResponse

from llm_wiki.domain.exceptions import (
    DomainException,
    DuplicateEntityError,
    EntityNotFoundError,
    ExternalServiceError,
    IngestionFailedError,
    InvalidStatusTransitionError,
    RateLimitError,
    ValidationError,
)

logger = logging.getLogger(__name__)

ERROR_MESSAGES = {
    EntityNotFoundError: "The requested resource was not found.",
    DuplicateEntityError: "A resource with that identifier already exists.",
    InvalidStatusTransitionError: "This operation is not allowed in the current state.",
    IngestionFailedError: "Content processing failed. The system will retry automatically.",
    ExternalServiceError: "An external service is temporarily unavailable.",
    RateLimitError: "Too many requests. Please wait before retrying.",
    ValidationError: "The request contains invalid data.",
}


async def domain_exception_handler(request: Request, exc: DomainException) -> JSONResponse:
    status_map = {
        EntityNotFoundError: 404,
        DuplicateEntityError: 409,
        InvalidStatusTransitionError: 400,
        IngestionFailedError: 500,
        ExternalServiceError: 502,
        RateLimitError: 429,
        ValidationError: 422,
    }
    status_code = status_map.get(type(exc), 500)
    logger.warning("Domain error: %s path=%s", type(exc).__name__, request.url.path, exc_info=True)
    return JSONResponse(
        status_code=status_code,
        content={
            "error": type(exc).__name__,
            "detail": ERROR_MESSAGES.get(type(exc), "An internal error occurred."),
        },
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled exception at %s", request.url.path)
    return JSONResponse(
        status_code=500,
        content={"error": "InternalServerError", "detail": "An unexpected error occurred."},
    )
