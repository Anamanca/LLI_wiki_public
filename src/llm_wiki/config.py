from pydantic import Field
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_file": ".env", "extra": "ignore"}

    database_url: str = ""

    minio_endpoint: str = Field(default="minio:9000", validation_alias="MINIO_ENDPOINT")
    minio_access_key: str = Field(default="", validation_alias="MINIO_ACCESS_KEY")
    minio_secret_key: str = Field(default="", validation_alias="MINIO_SECRET_KEY")

    ollama_host: str = Field(default="http://ollama:11434", validation_alias="OLLAMA_HOST")

    redis_host: str = Field(default="redis", validation_alias="REDIS_HOST")
    redis_port: int = Field(default=6379, validation_alias="REDIS_PORT")
    redis_password: str = Field(default="", validation_alias="REDIS_PASSWORD")

    @property
    def redis_url(self) -> str:
        pwd = f":{self.redis_password}@" if self.redis_password else ""
        return f"redis://{pwd}{self.redis_host}:{self.redis_port}/0"

    youtube_api_key: str = Field(default="", validation_alias="YOUTUBE_API_KEY")
    rapidapi_key: str = Field(default="", validation_alias="RAPIDAPI_KEY")
    telegram_bot_token: str = Field(default="", validation_alias="TELEGRAM_BOT_TOKEN")
    allowed_telegram_chat_ids: str = Field(default="", validation_alias="ALLOWED_TELEGRAM_CHAT_IDS")

    llm_provider: str = Field(default="auto", validation_alias="LLM_PROVIDER")
    opencode_api_key: str = Field(default="", validation_alias="OPENCODE_API_KEY")
    opencode_base_url: str = Field(
        default="https://opencode.ai/zen/go/v1", validation_alias="OPENCODE_BASE_URL"
    )
    opencode_primary_model: str = Field(
        default="deepseek-v4-flash", validation_alias="OPENCODE_PRIMARY_MODEL"
    )
    opencode_fallback_model: str = Field(
        default="deepseek-v4-flash", validation_alias="OPENCODE_FALLBACK_MODEL"
    )
    opencode_chat_model: str = Field(
        default="deepseek-v4-flash", validation_alias="OPENCODE_CHAT_MODEL"
    )

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_base_url: str = Field(
        default="https://generativelanguage.googleapis.com/v1beta/openai/",
        validation_alias="GEMINI_BASE_URL",
    )
    gemini_primary_model: str = Field(
        default="gemini-2.5-flash", validation_alias="GEMINI_PRIMARY_MODEL"
    )
    gemini_fallback_model: str = Field(
        default="gemini-2.5-flash", validation_alias="GEMINI_FALLBACK_MODEL"
    )
    gemini_chat_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_CHAT_MODEL")

    temporal_precision_enabled: bool = True
    reranker_enabled: bool = Field(default=True, validation_alias="RERANKER_ENABLED")
    cross_encoder_enabled: bool = Field(default=False, validation_alias="CROSS_ENCODER_ENABLED")
    reasoning_enabled: bool = Field(default=True, validation_alias="REASONING_ENABLED")

    # Wiki ingestion feature flags (default OFF — zero behavior change on deploy;
    # enable individually after canary). Do NOT use reasoning_enabled as the
    # wiki-only switch — it is global and defaults True.
    wiki_chunking_enabled: bool = Field(default=False, validation_alias="WIKI_CHUNKING_ENABLED")
    wiki_write_thinking_enabled: bool = Field(
        default=False, validation_alias="WIKI_WRITE_THINKING_ENABLED"
    )
    wiki_reflect_enabled: bool = Field(default=False, validation_alias="WIKI_REFLECT_ENABLED")

    # LangSmith observability & evaluation
    langsmith_tracing_enabled: bool = Field(default=False, validation_alias="LANGSMITH_TRACING")
    langsmith_api_key: str = Field(default="", validation_alias="LANGSMITH_API_KEY")
    langsmith_endpoint: str = Field(
        default="https://api.smith.langchain.com", validation_alias="LANGSMITH_ENDPOINT"
    )
    langsmith_project: str = Field(default="llm-wiki-rag", validation_alias="LANGSMITH_PROJECT")
    langsmith_evaluator_model: str = Field(
        default="deepseek-v4-flash", validation_alias="LANGSMITH_EVALUATOR_MODEL"
    )

    # Worker / ingestion operational settings
    cpu_max_percent: int = Field(default=85, validation_alias="CPU_MAX_PERCENT")
    rapidapi_rps: int = Field(default=1, validation_alias="RAPIDAPI_RPS")
    rapidapi_host: str = Field(
        default="youtube-transcriptor.p.rapidapi.com", validation_alias="RAPIDAPI_HOST"
    )
    worker_id: str = Field(default="", validation_alias="WORKER_ID")
    consumer_id: str = Field(default="", validation_alias="CONSUMER_ID")
    minio_bucket: str = Field(default="llm-wiki-media", validation_alias="MINIO_BUCKET")

    # Monitoring
    enable_metrics: bool = Field(default=False, validation_alias="ENABLE_METRICS")
    wiki_strict_validation: bool = Field(default=False, validation_alias="WIKI_STRICT_VALIDATION")
    log_format: str = Field(default="text", validation_alias="LOG_FORMAT")
    log_level: str = Field(default="INFO", validation_alias="LOG_LEVEL")


settings = Settings()
