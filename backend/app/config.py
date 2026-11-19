from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=PROJECT_ROOT / ".env", extra="ignore")

    # Where the app writes runtime state; tests and parallel sessions point it elsewhere.
    state_dir: Path = PROJECT_ROOT / ".state"
    # Read-only source data (billing plans, invoices, credit memos, exchange rates).
    data_dir: Path = PROJECT_ROOT / "data"

    # "mock" serves a scripted conversation without the LLM (make dev AGENT_BACKEND=mock).
    agent_backend: Literal["mock", "real"] = "real"

    # Any OpenAI-compatible endpoint. Copy model IDs exactly from the provider's catalog.
    llm_model: str = "anthropic/claude-sonnet-5.5"
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: str = ""

    @field_validator("state_dir", "data_dir")
    @classmethod
    def _resolve_state_dir(cls, v: Path) -> Path:
        # Relative to the project root, not the working directory (make dev runs from backend/).
        return v if v.is_absolute() else PROJECT_ROOT / v


settings = Settings()
