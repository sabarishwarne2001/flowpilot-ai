import json
from pydantic import field_validator, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.constants import APP_VERSION

# Signing keys that have been published and must never be accepted again,
# regardless of environment. This one was committed to app/core/config.py and
# .env.example in a public repository; every token it ever signed is forgeable
# by anyone who has read the history. Listing it here makes reuse a startup
# failure rather than a silent one. ARCH-03 Step 1.
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

    # CORS configurations (stored strictly as a string scalar)
    CORS_ORIGINS: str = "http://localhost:3000"

    # Database credential segments (PostgreSQL configurations)
    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "flowpilot"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    # Cryptography and Token Configurations (enforce obfuscated SecretStr)
    # No default. A signing key with a fallback value is a signing key that
    # ships to production unset. Validated in validate_identity_configuration.
    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"

    # Stays at 30 until Step 7. The plan moved it to 10 here, but a 10-minute
    # access token is only tolerable once refresh exists to renew it silently;
    # shortening it in this step would sign every user out three times an hour
    # for as long as Steps 6 and 7 are apart, with no compensating benefit —
    # nothing in this step consumes the shorter window.
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

    # ------------------------------------------------------------------
    # Refresh sessions (ARCH-03 §B.6, §B.7)
    # ------------------------------------------------------------------
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14

    #: How long after a rotation the superseded token is still tolerated.
    #: Two browser tabs refreshing within milliseconds of each other both
    #: present the same token; without this window the second one looks
    #: exactly like a stolen-token replay and signs the user out everywhere.
    #: Ten seconds is long enough to cover a tab race and short enough that a
    #: token captured off the wire is almost certainly already useless.
    SESSION_REUSE_GRACE_SECONDS: int = 10

    #: Bound on how far rotate_session will walk a rotation chain when
    #: resolving concurrent refreshes. A chain longer than this means the data
    #: is wrong, and walking it forever would hang the request.
    SESSION_CHAIN_WALK_LIMIT: int = 16

    # ------------------------------------------------------------------
    # Single-use identity tokens (ARCH-03 §B.2)
    # ------------------------------------------------------------------
    EMAIL_VERIFICATION_TTL_HOURS: int = 24

    #: Deliberately much shorter than verification. A reset link is a
    #: password-equivalent credential sitting in a mailbox; a verification link
    #: grants nothing on its own.
    PASSWORD_RESET_TTL_MINUTES: int = 60

    #: Per-user issuance ceiling within the window below, applied per purpose.
    #: Limits mailbox flooding and the offline guessing surface, without
    #: locking a legitimate user out of a second attempt.
    IDENTITY_TOKEN_MAX_PER_WINDOW: int = 5
    IDENTITY_TOKEN_WINDOW_MINUTES: int = 60

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
    OCR_LANGUAGE: str = "en"  # Standard default OCR language identifier

    # ------------------------------------------------------------------
    # Identity email — platform SMTP relay (ARCH-03 §B.1)
    # ------------------------------------------------------------------
    # Replaces the former SMTP_* block, which existed only as the fallback
    # branch of resolve_smtp_config and was never a deliberate platform
    # identity. Workspace SMTP lives in the email_settings table and sends on
    # behalf of a tenant; these credentials send on behalf of FlowPilot, to
    # people who may have no workspace at all.
    PLATFORM_SMTP_HOST: str = ""
    PLATFORM_SMTP_PORT: int = 587
    PLATFORM_SMTP_USERNAME: str = ""
    PLATFORM_SMTP_PASSWORD: SecretStr | None = None
    PLATFORM_SMTP_ENCRYPTION: str = "TLS"  # NONE | TLS | SSL

    # The visible sender, which on a hosted relay is not the login username.
    PLATFORM_SMTP_FROM_EMAIL: str = "noreply@flowpilot.ai"
    PLATFORM_SMTP_FROM_NAME: str = "FlowPilot AI"

    # Origin of the user-facing application. Identity links are built against
    # this, so a wrong value produces mail whose links point nowhere. Formerly
    # read via getattr(settings, "FRONTEND_HOST", "http://localhost:3000") in
    # notification_service, which silently sent every invitation to localhost.
    FRONTEND_URL: str = "http://localhost:3000"

    # SMTP Password Encryption
    EMAIL_ENCRYPTION_KEY: SecretStr

    # Sprint 5: AI Assistant & RAG Parameter Configurations
    RAG_TOP_K: int = 5
    RAG_SIMILARITY_THRESHOLD: float = 0.20  # Discards chunks with low relevance scores
    RAG_MAX_CONTEXT_LENGTH: int = 15000    # Maximum context characters to pass to prompts
    MAX_CONVERSATION_MESSAGES: int = 10    # Maximum historical messages loaded for chat memory
    MAX_CONVERSATION_TITLE_LENGTH: int = 150
    MAX_SOURCE_CITATIONS: int = 5
    LLM_TEMPERATURE: float = 0.2
    LLM_CLASSIFICATION_TEMPERATURE: float = 0.0
    LLM_ENTITY_EXTRACTION_TEMPERATURE: float = 0.1
    LLM_SUMMARIZATION_TEMPERATURE: float = 0.3
    GROQ_RAG_TEMPERATURE: float = 0.3
    GEMINI_RAG_TEMPERATURE: float = 0.2
    LLM_MAX_OUTPUT_TOKENS: int = 2048
    
    # Sprint 5: Token Tracking & Usage Analytics
    ENABLE_TOKEN_TRACKING: bool = True

    TOKEN_COST_PER_1K_INPUT: float = 0.0
    TOKEN_COST_PER_1K_OUTPUT: float = 0.0
    MAX_CONTEXT_CHUNKS_PER_DOCUMENT: int = 3
    
    RERANK_MIN_RESULTS: int = 4
    RERANK_MAX_CANDIDATES: int = 15
    RERANK_FINAL_RESULTS: int = 8
    
    DOCUMENT_FILTER_MARGIN: float = 0.25
    DOCUMENT_SCORE_TOP_K: int = 3

    #
    # Citation ranking
    #

    MAX_RESPONSE_CITATIONS: int = 3

    CITATION_RERANK_WEIGHT: float = 0.60

    CITATION_RRF_WEIGHT: float = 0.25

    CITATION_SEMANTIC_WEIGHT: float = 0.15

    #
    # Citation snippets
    #

    SNIPPET_MAX_SENTENCES: int = 2

    MAX_SNIPPET_LENGTH: int = 240

    #
    # Retrieval regression thresholds
    #
    RETRIEVAL_MIN_RECALL: float = 0.95

    RETRIEVAL_MIN_PRECISION: float = 0.90

    RETRIEVAL_MIN_MRR: float = 0.85

    RETRIEVAL_MAX_CONTAMINATION: float = 0.10

    RETRIEVAL_MAX_LATENCY_MS: float = 300.0

    RETRIEVAL_FAIL_FAST: bool = False

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

    @field_validator("PLATFORM_SMTP_ENCRYPTION")
    @classmethod
    def validate_platform_smtp_encryption(cls, v: str) -> str:
        """Constrains the platform relay transport to a supported mode."""
        normalized = v.strip().upper()
        if normalized not in {"NONE", "TLS", "SSL"}:
            raise ValueError(
                "PLATFORM_SMTP_ENCRYPTION must be one of NONE, TLS, or SSL."
            )
        return normalized

    @field_validator("FRONTEND_URL")
    @classmethod
    def validate_frontend_url(cls, v: str) -> str:
        """
        Normalizes the application origin and rejects unusable values early.

        The trailing slash is stripped here rather than at every call site, so
        link builders can concatenate a path without producing a double slash.
        """
        cleaned = v.strip().rstrip("/")
        if not cleaned.startswith(("http://", "https://")):
            raise ValueError(
                "FRONTEND_URL must include a scheme, e.g. https://app.flowpilot.ai"
            )
        return cleaned

    @model_validator(mode="after")
    def validate_identity_configuration(self) -> "Settings":
        """
        Enforces the invariants ARCH-03 depends on, at import time.

        These are checked here rather than at first use because the failure
        modes are all silent: a compromised signing key produces tokens that
        verify perfectly, an unset relay produces registrations that appear to
        succeed, and a plaintext origin produces refresh cookies that are never
        sent back. None of them raise on their own.
        """
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