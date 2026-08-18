from dependency_injector import containers, providers

from llm_wiki.application.use_cases.ingestion.extract_events import ExtractEventsUseCase
from llm_wiki.application.use_cases.ingestion.integrate_wiki import IntegrateWikiUseCase
from llm_wiki.application.use_cases.ingestion.process_video import (
    ProcessVideoUseCase,
    RetryableIngestion,
)
from llm_wiki.application.use_cases.query.ask_question import AskQuestionUseCase
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.use_cases.query.stream_answer import StreamAnswerUseCase
from llm_wiki.config import settings
from llm_wiki.infrastructure.embedding.ollama_adapter import OllamaEmbeddingAdapter
from llm_wiki.infrastructure.llm.managed_llm_adapter import ManagedLLMAdapter
from llm_wiki.infrastructure.persistence.file import ChatSessionFileRepository
from llm_wiki.infrastructure.persistence.postgres.database import get_db  # noqa: F401
from llm_wiki.infrastructure.persistence.redis.cache_adapter import RedisCacheAdapter
from llm_wiki.infrastructure.telemetry import create_telemetry_adapter


def traced_llm(name: str = "llm_chat_completion"):
    from llm_wiki.infrastructure.llm.traced_llm_wrapper import TracedLLMWrapper

    return TracedLLMWrapper(
        container.llm_client(),
        container.telemetry(),
        model=container.config.opencode_primary_model() or "unknown",
    )


def traced_embedder(name: str = "embedding"):
    from llm_wiki.infrastructure.embedding.traced_embedding_wrapper import TracedEmbeddingWrapper

    return TracedEmbeddingWrapper(
        container.embedder(),
        container.telemetry(),
        model=container.config.langsmith_evaluator_model() or "unknown",
    )


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    telemetry = providers.Singleton(
        create_telemetry_adapter,
        enabled=settings.langsmith_tracing_enabled,
        api_key=settings.langsmith_api_key,
        api_url=settings.langsmith_endpoint,
        project=settings.langsmith_project,
    )

    embedder = providers.Singleton(OllamaEmbeddingAdapter, host=settings.ollama_host)

    llm_client = providers.Singleton(ManagedLLMAdapter)

    cache = providers.Singleton(RedisCacheAdapter)

    chat_session_repo = providers.Singleton(ChatSessionFileRepository)

    query_pipeline = providers.Factory(
        QueryPipeline,
        embedder=embedder,
        vector_search=None,
        keyword_search=None,
        llm=llm_client,
        cache=cache,
        telemetry=telemetry,
    )

    ask_question_use_case = providers.Factory(
        AskQuestionUseCase,
        pipeline=query_pipeline,
    )

    stream_answer_use_case = providers.Factory(
        StreamAnswerUseCase,
        pipeline=query_pipeline,
    )

    retry_handler = providers.Factory(
        RetryableIngestion,
        source_item_repo=None,
    )

    integrate_wiki_use_case = providers.Factory(
        IntegrateWikiUseCase,
        page_repo=None,
        section_repo=None,
        event_repo=None,
        entity_repo=None,
        embedder=embedder,
    )

    extract_events_use_case = providers.Factory(
        ExtractEventsUseCase,
        event_repo=None,
        entity_repo=None,
        llm=llm_client,
        embedder=embedder,
        telemetry=telemetry,
    )

    process_video_use_case = providers.Factory(
        ProcessVideoUseCase,
        source_item_repo=None,
        retry_handler=retry_handler,
        wiki_integrator=integrate_wiki_use_case,
        embedder=embedder,
        llm=llm_client,
    )


container = Container()
container.config.from_pydantic(settings)
