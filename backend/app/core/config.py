import json
from pydantic import field_validator, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.constants import APP_VERSION

class Settings(BaseSettings):
    """
    Application settings container managing environment parsing and validation.
    Integrates with Pydantic Settings v2 and enforces strict SecretStr security on tokens.
    """
    PROJECT_NAME: str = "FlowPilot AI"
    APP_VERSION: str = APP_VERSION
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

    # Cryptography and Token Configurations (enforce obfuscated SecretStr)
    JWT_SECRET_KEY: SecretStr = SecretStr("e839e248b9409893d5f84893708e983cf4b1b88e17409c914e963df9bc0297da")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # File Ingestion & Storage Configurations
    UPLOAD_DIR: str = "uploads"
    MAX_UPLOAD_SIZE: int = 104857600  # 100 MB in bytes
    ALLOWED_MIME_TYPES: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg"
    ]

    # Sprint 3: AI Processing & LLM Gateways
    GROQ_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None
    LLM_PROVIDER: str = "groq"  # Supported drivers: "groq", "gemini"
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL_NAME: str = "gemini-1.5-flash"

    # Sprint 3: Text Chunking & Embedding Model Configurations
    CHUNK_SIZE: int = 1000
    CHUNK_OVERLAP: int = 100
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32

    # Sprint 3: Vector Store (ChromaDB) Configurations
    CHROMA_PERSIST_DIRECTORY: str = "chromadb"
    CHROMA_COLLECTION_NAME: str = "flowpilot_chunks"

    CHROMA_TELEMETRY_ENABLED: bool = False
    CHROMA_ALLOW_RESET: bool = False

    # Sprint 3: OCR Configurations
    OCR_LANGUAGE: str = "en"  # Standard default OCR language identifier

    # Sprint 4: SMTP Email Configurations
    SMTP_HOST: str = "localhost"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: SecretStr | None = None
    SMTP_FROM_EMAIL: str = "noreply@flowpilot.ai"
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT: int = 10

    # Sprint 5: AI Assistant & RAG Parameter Configurations
    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.4  # Discards chunks with low relevance scores
    RAG_MAX_CONTEXT_LENGTH: int = 15000    # Maximum context characters to pass to prompts
    MAX_CONVERSATION_MESSAGES: int = 10    # Maximum historical messages loaded for chat memory
    MAX_CONVERSATION_TITLE_LENGTH: int = 150
    MAX_SOURCE_CITATIONS: int = 5
    LLM_TEMPERATURE: float = 0.2
    LLM_MAX_OUTPUT_TOKENS: int = 2048

    # --- Pydantic Field Validators ---

    @field_validator("RAG_TOP_K")
    @classmethod
    def validate_rag_top_k(cls, v: int) -> int:
        """Enforces that the vector retrieval search limit is at least 1."""
        if v < 1:
            raise ValueError("RAG_TOP_K must be greater than or equal to 1.")
        return v

    @field_validator("RAG_SIMILARITY_THRESHOLD")
    @classmethod
    def validate_rag_similarity_threshold(cls, v: float) -> float:
        """Enforces that the semantic similarity match index is between 0.0 and 1.0 inclusive."""
        if not (0.0 <= v <= 1.0):
            raise ValueError("RAG_SIMILARITY_THRESHOLD must reside strictly between 0.0 and 1.0 inclusive.")
        return v

    @field_validator("RAG_MAX_CONTEXT_LENGTH")
    @classmethod
    def validate_rag_max_context_length(cls, v: int) -> int:
        """Enforces that the character budget allocation is greater than 1000 characters."""
        if v <= 1000:
            raise ValueError("RAG_MAX_CONTEXT_LENGTH must be strictly greater than 1000 characters.")
        return v

    @field_validator("MAX_CONVERSATION_MESSAGES")
    @classmethod
    def validate_max_conversation_messages(cls, v: int) -> int:
        """Enforces that the historical memory lookup window retains at least 1 message."""
        if v < 1:
            raise ValueError("MAX_CONVERSATION_MESSAGES must be greater than or equal to 1.")
        return v

    # --- Custom Model Properties ---

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
    def sqlalchemy_database_uri(self) -> str:
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