import json
from typing import Any, Union
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.constants import APP_VERSION as DEFAULT_APP_VERSION

class Settings(BaseSettings):
    """
    Application settings container managing environment parsing and validation.
    Integrates with Pydantic Settings v2.
    """
    PROJECT_NAME: str = "FlowPilot AI"
    APP_VERSION: str = DEFAULT_APP_VERSION
    API_TITLE: str = "FlowPilot AI Core API"
    ENVIRONMENT: str = "development"
    API_V1_STR: str = "/api/v1"
    
    # Server runtime configurations
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    # CORS configurations (stored strictly as a string scalar)
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database credential segments (PostgreSQL configurations)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "flowpilot"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Cryptography and Token Configurations
    JWT_SECRET_KEY: str = "e839e248b9409893d5f84893708e983cf4b1b88e17409c914e963df9bc0297da"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    @property
    def cors_origins(self) -> list[str]:
        """
        Parses the raw CORS_ORIGINS environment string into a list of origins.
        Handles both standardized comma-separated strings and JSON-formatted arrays.
        """
        raw_origins = self.CORS_ORIGINS.strip()
        if not raw_origins:
            return []

        # Check for JSON array string syntax
        if raw_origins.startswith("[") and raw_origins.endswith("]"):
            try:
                parsed = json.loads(raw_origins)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed]
            except json.JSONDecodeError:
                pass

        # Parse standard comma-separated sequences
        return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    @property
    def database_url(self) -> str:
        """
        Dynamically construct the database connection URI string.
        Resolves individual credential segments without exposing hardcoded URL connections.
        """
        return (
            f"postgresql://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    # Configure Pydantic settings loading behaviors
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()