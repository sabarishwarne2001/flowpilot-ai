import json
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

    # Configure Pydantic settings loading behaviors
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()