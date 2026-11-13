# src/quantforge/config/settings.py

from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    Validated at startup — missing required values raise an error immediately.
    """

    # --- Database ---
    database_url: str = Field(
        ...,
        description="PostgreSQL connection string",
    )

    # --- LLM ---
    openrouter_api_key: str = Field(
        ...,
        description="OpenRouter API key for LLM access",
    )

    # --- Research Sources ---
    # Optional — arXiv and OpenAlex need no key
    # Crossref polite pool accepts an email for higher rate limits
    crossref_mailto: str | None = Field(
        default=None,
        description="Email for Crossref polite pool (optional)",
    )

    # --- Environment ---
    quantforge_env: str = Field(
        default="development",
        description="Runtime environment: development | production",
    )
    log_level: str = Field(
        default="INFO",
        description="Logging level: DEBUG | INFO | WARNING | ERROR",
    )

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "case_sensitive": False,
    }


# Single shared instance — imported everywhere that needs settings
settings = Settings()