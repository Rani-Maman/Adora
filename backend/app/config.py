"""
Application configuration using Pydantic Settings.
All secrets are loaded from environment variables.
"""

from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Database (required)
    db_host: str
    db_port: int = 5432
    db_name: str
    db_user: str
    db_password: str

    # API Keys
    firecrawl_api_key: str
    groq_api_key: str | None = None
    gemini_api_key: str | None = None

    # Extension API key (for authenticating Chrome extension requests)
    adora_api_key: str | None = None

    # Email (optional)
    email_sender: str | None = None
    email_password: str | None = None
    email_recipient: str | None = None
    smtp_server: str = "smtp.gmail.com"
    smtp_port: int = 587

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        case_sensitive = False


@lru_cache
def get_settings() -> Settings:
    """Get cached settings instance."""
    return Settings()
