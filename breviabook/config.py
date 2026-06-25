"""Configuration: pydantic-settings backed by environment variables / .env.

Mirrors docs/ROADMAP.md §9. API-key fields accept a comma-separated list so a future
key-rotation pool (Phase 9) can consume them; ``keys_for`` does the splitting.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ImageStrategy(StrEnum):
    """How images are kept in the condensed output (ROADMAP §7.1)."""

    KEEP_REFERENCED = "keep_referenced"
    VISION_RANKED = "vision_ranked"


class Settings(BaseSettings):
    """Runtime configuration, populated from environment / ``.env``."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Provider selection ---
    llm_provider: str = "ollama"
    ollama_endpoint: str = "http://localhost:11434"
    default_model: str = "gemma4:e4b"

    # --- API keys (comma-separated lists allowed; rotation pool consumes them) ---
    openai_api_key: str = ""
    gemini_api_key: str = ""
    openrouter_api_key: str = ""

    # --- Condensation defaults ---
    default_target_ratio: float = Field(default=0.30, gt=0.0, le=1.0)
    default_chunk_tokens: int = Field(default=2000, gt=0)
    image_strategy: ImageStrategy = ImageStrategy.KEEP_REFERENCED

    def keys_for(self, provider: str) -> list[str]:
        """Return the list of API keys configured for ``provider`` (rotation-ready)."""
        raw = {
            "openai": self.openai_api_key,
            "gemini": self.gemini_api_key,
            "openrouter": self.openrouter_api_key,
        }.get(provider.lower(), "")
        return [k.strip() for k in raw.split(",") if k.strip()]


def load_settings() -> Settings:
    """Load settings from the environment / ``.env``."""
    return Settings()
