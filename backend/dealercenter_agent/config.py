from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LLM_", env_file=".env", extra="ignore")

    provider: Literal["openai", "anthropic", "google"] = "anthropic"
    model: str = "claude-sonnet-5"
    system_prompt_path: str = "prompts/recovery_agent.md"
    api_key: SecretStr = SecretStr("")


class BrowserConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BROWSER_", env_file=".env", extra="ignore")

    engine: Literal["playwright"] = "playwright"
    headless: bool = True
    auth_state_path: Path = Path("./.auth_state.json")


class DBConfig(BaseSettings):
    """Connection details come straight from DATABASE_URL — Supabase in dev,
    Postgres/MySQL in prod. DB_BACKEND is a dialect label for behavior that
    might ever need to branch on it; it is never used to build the URL."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    db_backend: Literal["supabase", "postgresql", "mysql"] = "supabase"
    database_url: str = ""


class StorageConfig(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    storage_backend: Literal["local"] = "local"
    storage_local_path: Path = Path("./invoice_pdfs")


class QueueConfig(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="QUEUE_", env_file=".env", extra="ignore")

    redis_url: str = "redis://localhost:6379/0"


class AppConfig:
    """Aggregates the four config slices. Each slice loads its own env vars
    independently (see each class's env_prefix), so this is just a
    convenience bundle, not a second source of truth."""

    def __init__(self) -> None:
        self.llm = LLMConfig()
        self.browser = BrowserConfig()
        self.db = DBConfig()
        self.storage = StorageConfig()
        self.queue = QueueConfig()
