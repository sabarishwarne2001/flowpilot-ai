import json
from pathlib import Path
from pydantic import field_validator, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.constants import APP_VERSION

LEAKED_JWT_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "e839e248b9409893d5f84893708e983cf4b1b88e17409c914e963df9bc0297da",
    }
)


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

    # CORS configurations
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database credential segments (PostgreSQL configurations)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "flowpilot"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Cryptography and Token Configurations
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10

    # Refresh sessions
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    SESSION_REUSE_GRACE_SECONDS: int = 10
    SESSION_CHAIN_WALK_LIMIT: int = 16

    # Single-use identity tokens
    EMAIL_VERIFICATION_TTL_HOURS: int = 24
    PASSWORD_RESET_TTL_MINUTES: int = 60
    IDENTITY_TOKEN_MAX_PER_WINDOW: int = 5
    IDENTITY_TOKEN_WINDOW_MINUTES: int = 60

    # ARCH-04 invitation lifecycle
    INVITATION_TTL_HOURS: int = 72
    INVITATION_RESEND_COOLDOWN_MINUTES: int = 5
    INVITATION_MAX_GRANTS: int = 50
    INVITATION_RETENTION_DAYS: int = 180

    # ARCH-05 ownership transfer
    OWNERSHIP_TRANSFER_TTL_DAYS: int = 7

    # ARCH-07 Step 5 Storage Driver Configurations
    STORAGE_BACKEND: str = "local"
    UPLOAD_DIR: Path = Path("uploads")
    STORAGE_QUARANTINE_DIR: Path = Path("uploads/quarantine")

    # File Ingestion & Storage Configurations
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
    LLM_PROVIDER: str = "groq"
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL_NAME: str = "gemini-3.5-flash"

    # Sprint 3: Text Chunking & Embedding Model Configurations
    CHUNK_SIZE: int = 750
    CHUNK_OVERLAP: int = 150
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32

    # Sprint 3: Vector Store (ChromaDB) Configurations
    CHROMA_PERSIST_DIRECTORY: str = "chromadb"
    CHROMA_COLLECTION_NAME: str = "flowpilot_chunks"
    CHROMA_TELEMETRY_ENABLED: bool = False
    CHROMA_ALLOW_RESET: bool = False

    # Sprint 3: OCR Configurations
    OCR_LANGUAGE: str = "en"

    # Identity email — platform SMTP relay
    PLATFORM_SMTP_HOST: str = ""
    PLATFORM_SMTP_PORT: int = 587
    PLATFORM_SMTP_USERNAME: str = ""
    PLATFORM_SMTP_PASSWORD: SecretStr | None = None
    PLATFORM_SMTP_ENCRYPTION: str = "TLS"

    PLATFORM_SMTP_FROM_EMAIL: str = "noreply@flowpilot.ai"
    PLATFORM_SMTP_FROM_NAME: str = "FlowPilot AI"

    FRONTEND_URL: str = "http://localhost:3000"

    # SMTP Password Encryption
    EMAIL_ENCRYPTION_KEY: SecretStr

    # Sprint 5: AI Assistant & RAG Parameter Configurations
    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.20
    RAG_MAX_CONTEXT_LENGTH: int = 15000
    MAX_CONVERSATION_MESSAGES: int = 10
    MAX_CONVERSATION_TITLE_LENGTH: int = 150
    MAX_SOURCE_CITATIONS: int = 5
    LLM_TEMPERATURE: float = 0.2
    LLM_CLASSIFICATION_TEMPERATURE: float = 0.0
    LLM_ENTITY_EXTRACTION_TEMPERATURE: float = 0.1
    LLM_SUMMARIZATION_TEMPERATURE: float = 0.3
    GROQ_RAG_TEMPERATURE: float = 0.3
    GEMINI_RAG_TEMPERATURE: float = 0.2
    LLM_MAX_OUTPUT_TOKENS: int = 2048
    
    ENABLE_TOKEN_TRACKING: bool = True
    TOKEN_COST_PER_1K_INPUT: float = 0.0
    TOKEN_COST_PER_1K_OUTPUT: float = 0.0
    MAX_CONTEXT_CHUNKS_PER_DOCUMENT: int = 3
    
    RERANK_MIN_RESULTS: int = 4
    RERANK_MAX_CANDIDATES: int = 15
    RERANK_FINAL_RESULTS: int = 8
    
    DOCUMENT_FILTER_MARGIN: float = 0.25
    DOCUMENT_SCORE_TOP_K: int = 3

    MAX_RESPONSE_CITATIONS: int = 3
    CITATION_RERANK_WEIGHT: float = 0.60
    CITATION_RRF_WEIGHT: float = 0.25
    CITATION_SEMANTIC_WEIGHT: float = 0.15

    SNIPPET_MAX_SENTENCES: int = 2
    MAX_SNIPPET_LENGTH: int = 240

    RETRIEVAL_MIN_RECALL: float = 0.95
    RETRIEVAL_MIN_PRECISION: float = 0.90
    RETRIEVAL_MIN_MRR: float = 0.85
    RETRIEVAL_MAX_CONTAMINATION: float = 0.10
    RETRIEVAL_MAX_LATENCY_MS: float = 300.0
    RETRIEVAL_FAIL_FAST: bool = False

    @field_validator("RAG_TOP_K")
    @classmethod
    def validate_rag_top_k(cls, v: int) -> int:
        if v < 1:
            raise ValueError("RAG_TOP_K must be greater than or equal to 1.")
        return v

    @field_validator("RAG_SIMILARITY_THRESHOLD")
    @classmethod
    def validate_rag_similarity_threshold(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError("RAG_SIMILARITY_THRESHOLD must reside strictly between 0.0 and 1.0 inclusive.")
        return v

    @field_validator("RAG_MAX_CONTEXT_LENGTH")
    @classmethod
    def validate_rag_max_context_length(cls, v: int) -> int:
        if v <= 1000:
            raise ValueError("RAG_MAX_CONTEXT_LENGTH must be strictly greater than 1000 characters.")
        return v

    @field_validator("MAX_CONVERSATION_MESSAGES")
    @classmethod
    def validate_max_conversation_messages(cls, v: int) -> int:
        if v < 1:
            raise ValueError("MAX_CONVERSATION_MESSAGES must be greater than or equal to 1.")
        return v

    @field_validator("PLATFORM_SMTP_ENCRYPTION")
    @classmethod
    def validate_platform_smtp_encryption(cls, v: str) -> str:
        normalized = v.strip().upper()
        if normalized not in {"NONE", "TLS", "SSL"}:
            raise ValueError(
                "PLATFORM_SMTP_ENCRYPTION must be one of NONE, TLS, or SSL."
            )
        return normalized

    @field_validator("FRONTEND_URL")
    @classmethod
    def validate_frontend_url(cls, v: str) -> str:
        cleaned = v.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                "FRONTEND_URL must include a scheme, e.g. https://app.flowpilot.ai"
            )
        return cleaned

    @model_validator(mode="after")
    def validate_identity_configuration(self) -> "Settings":
        secret = self.JWT_SECRET_KEY.get_secret_value()

        if secret in LEAKED_JWT_SECRET_KEYS:
            raise ValueError(
                "JWT_SECRET_KEY is a published value and cannot be used. "
                "Generate a new key with: openssl rand -hex 32"
            )

        if len(secret) < 32:
            raise ValueError(
                "JWT_SECRET_KEY must be at least 32 characters. "
                "Generate one with: openssl rand -hex 32"
            )

        if self.ENVIRONMENT != "development":
            if not self.PLATFORM_SMTP_HOST.strip():
                raise ValueError(
                    "PLATFORM_SMTP_HOST is required outside development. "
                    "Without it no user can verify an address or reset a "
                    "password."
                )
            if not self.FRONTEND_URL.startswith("https://"):
                raise ValueError(
                    "FRONTEND_URL must use HTTPS outside development. The "
                    "refresh cookie is issued Secure and will not be sent "
                    "back over plaintext HTTP."
                )

        return self

    @property
    def cors_origins(self) -> list[str]:
        raw_origins = self.CORS_ORIGINS.strip()
        if not raw_origins:
            return []

        if raw_origins.startswith("[") and raw_origins.endswith("]"):
            try:
                parsed = json.loads(raw_origins)
                if isinstance(parsed, list):
                    return [str(item).strip() for item in parsed]
            except json.JSONDecodeError:
                pass

        return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]

    @property
    def sqlalchemy_database_uri(self) -> str:
        return (
            f"postgresql://"
            f"{self.POSTGRES_USER}:{self.POSTGRES_PASSWORD}@"
            f"{self.POSTGRES_HOST}:{self.POSTGRES_PORT}/"
            f"{self.POSTGRES_DB}"
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore"
    )

settings = Settings()