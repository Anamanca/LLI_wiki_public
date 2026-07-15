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
    opencode_base_url: str = Field(default="https://opencode.ai/zen/go/v1", validation_alias="OPENCODE_BASE_URL")
    opencode_primary_model: str = Field(default="deepseek-v4-flash", validation_alias="OPENCODE_PRIMARY_MODEL")
    opencode_fallback_model: str = Field(default="deepseek-v4-flash", validation_alias="OPENCODE_FALLBACK_MODEL")
    opencode_chat_model: str = Field(default="deepseek-v4-flash", validation_alias="OPENCODE_CHAT_MODEL")

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    gemini_base_url: str = Field(default="https://generativelanguage.googleapis.com/v1beta/openai/", validation_alias="GEMINI_BASE_URL")
    gemini_primary_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_PRIMARY_MODEL")
    gemini_fallback_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_FALLBACK_MODEL")
    gemini_chat_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_CHAT_MODEL")

    temporal_precision_enabled: bool = True
    reranker_enabled: bool = Field(default=True, validation_alias="RERANKER_ENABLED")


settings = Settings()
