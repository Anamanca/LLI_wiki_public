import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from llm_wiki.domain.exceptions import DomainException
from llm_wiki.presentation.middleware.error_handler import domain_exception_handler, unhandled_exception_handler
from llm_wiki.presentation.middleware.request_logging import RequestLoggingMiddleware
from llm_wiki.presentation.routes import query, health, sources, pages, search, admin, chat_sessions
from llm_wiki.presentation.routes.admin import restart_source

app = FastAPI(title="LLM Wiki", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.add_exception_handler(DomainException, domain_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)

app.include_router(health.router, prefix="/api", tags=["health"])
app.include_router(admin.router, prefix="/api", tags=["admin"])
app.add_api_route("/api/restart/source/{source_id}", restart_source, methods=["POST"], tags=["admin"])
app.include_router(query.router, prefix="/api", tags=["query"])
app.include_router(sources.router, prefix="/api", tags=["sources"])
app.include_router(pages.router, prefix="/api", tags=["pages"])
app.include_router(search.router, prefix="/api", tags=["search"])
app.include_router(chat_sessions.router, prefix="/api", tags=["chat_sessions"])

if os.getenv("ENABLE_STUB_ROUTES", "false").lower() in ("1", "true", "yes"):
    from llm_wiki.presentation.routes import stubs  # noqa: E402

    app.include_router(stubs.router, prefix="/api", tags=["stubs"])
