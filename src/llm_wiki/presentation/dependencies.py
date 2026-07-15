from dependency_injector import containers, providers

from llm_wiki.config import settings
from llm_wiki.infrastructure.persistence.postgres.database import async_session_factory
from llm_wiki.infrastructure.persistence.postgres.repositories.source_repository import PostgresSourceRepository, PostgresSourceItemRepository
from llm_wiki.infrastructure.persistence.postgres.repositories.page_repository import PostgresPageRepository, PostgresPageSectionRepository
from llm_wiki.infrastructure.persistence.postgres.repositories.event_repository import PostgresEventRepository
from llm_wiki.infrastructure.persistence.postgres.repositories.entity_repository import PostgresEntityRepository
from llm_wiki.infrastructure.search.pgvector_adapter import PgVectorSearchAdapter
from llm_wiki.infrastructure.search.tsvector_adapter import TsVectorSearchAdapter
from llm_wiki.infrastructure.llm.openai_adapter import OpenAIAdapter
from llm_wiki.infrastructure.embedding.ollama_adapter import OllamaEmbeddingAdapter
from llm_wiki.infrastructure.persistence.redis.cache_adapter import RedisCacheAdapter
from llm_wiki.application.use_cases.query.pipeline import QueryPipeline
from llm_wiki.application.use_cases.query.ask_question import AskQuestionUseCase
from llm_wiki.application.use_cases.query.stream_answer import StreamAnswerUseCase
from llm_wiki.application.use_cases.ingestion.integrate_wiki import IntegrateWikiUseCase
from llm_wiki.application.use_cases.ingestion.process_video import ProcessVideoUseCase, RetryableIngestion
from llm_wiki.application.use_cases.ingestion.extract_events import ExtractEventsUseCase


class Container(containers.DeclarativeContainer):
    config = providers.Configuration()

    embedder = providers.Singleton(OllamaEmbeddingAdapter, host=settings.ollama_host)

    llm_client = providers.Singleton(
        OpenAIAdapter,
        api_key=settings.opencode_api_key,
        base_url=settings.opencode_base_url,
        model=settings.opencode_primary_model,
    )

    cache = providers.Singleton(RedisCacheAdapter)

    query_pipeline = providers.Factory(
        QueryPipeline,
        embedder=embedder,
        vector_search=None,
        keyword_search=None,
        llm=llm_client,
        cache=cache,
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

from llm_wiki.infrastructure.persistence.postgres.database import get_db  # noqa: E402

