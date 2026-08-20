import json
import os
import warnings
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from pydantic import field_validator, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from app.core.constants import APP_VERSION

LEAKED_JWT_SECRET_KEYS: frozenset[str] = frozenset(
    {
        "e839e248b9409893d5f84893708e983cf4b1b88e17409c914e963df9bc0297da",
    }
)

_KEYLESS_ENVIRONMENTS: frozenset[str] = frozenset({"test", "development"})


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

    # API Key & Redis Identity Peppers (ARCH-08 §B.4, §0.2)
    API_KEY_PEPPER: SecretStr = SecretStr("flowpilot_default_api_key_pepper_secret_2026")
    REDIS_IDENTITY_PEPPER: SecretStr = SecretStr("flowpilot_default_redis_identity_pepper_2026")

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

    # ARCH-07 Step 11 Maintenance Sweeper Configurations
    AUDIT_SWEEPER_DATABASE_URL: Optional[SecretStr] = None
    FILE_RECLAMATION_DAYS: int = 30

    # File Ingestion & Storage Configurations
    MAX_UPLOAD_SIZE: int = 104857600
    ALLOWED_MIME_TYPES: list[str] = [
        "application/pdf",
        "image/png",
        "image/jpeg",
        "image/jpg",
        "image/tiff",
        "image/webp",
        "image/gif",
        "image/bmp",
    ]

    # Sprint 3: AI Processing & LLM Gateways
    GROQ_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None
    LLM_PROVIDER: str = "groq"
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL_NAME: str = "gemini-3.5-flash"

    # Sprint 3: Embedding Model Configurations
    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32

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
    EMAIL_ENCRYPTION_KEYS: Optional[SecretStr] = SecretStr("v3-Q90I2S6bXpL9_L3_0V8gJ0Z1P8yL1_L3_0V8gJ0Z=")

    # Redis & Rate Limiter Configurations (ARCH-08 Step 6)
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_BACKEND: str = "redis"
    REDIS_URL: Optional[SecretStr] = SecretStr("redis://localhost:6379/0")
    REDIS_SOCKET_TIMEOUT_SECONDS: float = 0.25
    REDIS_MAX_CONNECTIONS: int = 50

    TRUSTED_PROXY_HOPS: int = 0

    RATE_LIMIT_GLOBAL_IP_PER_MINUTE: int = 600
    RATE_LIMIT_USER_PER_MINUTE: int = 300
    RATE_LIMIT_LOGIN_IP_PER_5MIN: int = 20
    RATE_LIMIT_CREDENTIAL_PER_HOUR: int = 10
    RATE_LIMIT_EXPORT_PER_HOUR: int = 5

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

    # --- ARCH-10 Step 3: default spend ceilings -------------------------
    SPEND_DEFAULT_MONTHLY_COST_MICROS: Optional[int] = 25_000_000
    SPEND_DEFAULT_DAILY_COST_MICROS: Optional[int] = 5_000_000
    SPEND_DEFAULT_MONTHLY_OCR_PAGES: Optional[int] = 2_000
    SPEND_DEFAULT_MONTHLY_LLM_INPUT_TOKENS: Optional[int] = 2_000_000
    SPEND_DEFAULT_MONTHLY_LLM_OUTPUT_TOKENS: Optional[int] = 500_000
    SPEND_DEFAULT_MONTHLY_EMBEDDING_TOKENS: Optional[int] = 5_000_000

    def spend_default_quantities(self) -> dict[str, Optional[int]]:
        return {
            "ocr.page": self.SPEND_DEFAULT_MONTHLY_OCR_PAGES,
            "llm.input_token": self.SPEND_DEFAULT_MONTHLY_LLM_INPUT_TOKENS,
            "llm.output_token": self.SPEND_DEFAULT_MONTHLY_LLM_OUTPUT_TOKENS,
            "embedding.token": self.SPEND_DEFAULT_MONTHLY_EMBEDDING_TOKENS,
        }

    # --- ARCH-10 Step 4: object storage ---------------------------------
    STORAGE_BACKEND: str = "local"
    S3_BUCKET: Optional[str] = None
    S3_REGION: Optional[str] = "auto"
    S3_ENDPOINT_URL: Optional[str] = None
    S3_PREFIX: str = ""
    S3_SERVER_SIDE_ENCRYPTION: Optional[str] = "AES256"
    S3_MAX_POOL_CONNECTIONS: int = 20
    S3_MULTIPART_THRESHOLD: int = 16 * 1024 * 1024
    S3_MULTIPART_CHUNKSIZE: int = 16 * 1024 * 1024
    S3_MAX_CONCURRENCY: int = 4
    STORAGE_SAMPLE_INTERVAL_MINUTES: int = 60

    # --- ARCH-10 Step 5: validation -------------------------------------
    MAX_DOCUMENT_PAGES: int = 500
    SCRUB_UPLOAD_METADATA: bool = True

    # --- ARCH-10 Step 6: OCR --------------------------------------------
    OCR_PROVIDER: str = "paddleocr"
    OCR_MAX_PAGES_PER_DOCUMENT: int = 500
    OCR_JOB_MAX_ATTEMPTS: int = 5
    OCR_GUARD_BEFORE_EXTRACTION: bool = False

    # --- ARCH-11 Step 1: embedding metering & the frozen baseline --------
    EMBEDDING_METERING_ENABLED: bool = True
    EMBEDDING_MAX_SEQUENCE_TOKENS: Optional[int] = None
    EMBEDDING_DIMENSION: int = 384
    GOLDEN_SET_PATH: str = "evaluation/golden/arch11_golden_v1.json"
    RETRIEVAL_BASELINE_DIR: str = "evaluation/baselines"

    # --- ARCH-11 Step 2: pgvector ----------------------------------------
    DOCUMENT_CHUNK_PARTITIONS: int = 16
    HNSW_ITERATIVE_SCAN: str = "relaxed_order"
    HNSW_EF_SEARCH: int = 40
    APPLY_HNSW_SESSION_DEFAULTS: bool = True

    # --- ARCH-11 Step 3: chunking ---------------------------------------
    CHUNK_SIZE_TOKENS: int = 220
    CHUNK_OVERLAP_PCT: int = 10

    # --- ARCH-11 Step 4: backfill ---------------------------------------
    SPEND_PLATFORM_MONTHLY_BACKFILL_TOKENS: Optional[int] = 200_000_000

    # --- ARCH-11 Step 5: lexical retrieval -------------------------------
    LEXICAL_TSVECTOR_CONFIG: str = "english"
    LEXICAL_TRIGRAM_THRESHOLD: float = 0.3
    LEXICAL_CANDIDATES: int = 50

    # --- ARCH-11 Step 6: hybrid fusion ------------------------------------
    HYBRID_CANDIDATES: int = 150
    HYBRID_WEIGHT_DENSE: float = 1.0
    HYBRID_WEIGHT_FULL_TEXT: float = 1.0
    HYBRID_WEIGHT_FUZZY: float = 0.5

    # --- ARCH-11 Step 7: reranker service ---------------------------------
    RERANKER_ENABLED: bool = True
    RERANKER_URL: str = "http://reranker:8081"
    RERANKER_INTERNAL_TOKEN: Optional[SecretStr] = None
    RERANKER_TIMEOUT: float = 2.0
    RERANKER_CONNECT_TIMEOUT: float = 0.5
    RERANKER_BREAKER_THRESHOLD: int = 5
    RERANKER_BREAKER_RESET_SECONDS: float = 30.0

    # --- ARCH-11 hardening: context assembly ------------------------------
    CONTEXT_INJECTION_BLOCK_THRESHOLD: int = 3

    # --- ARCH-11.5 Step 1: LLM metering -----------------------------------
    LLM_METERING_ENABLED: bool = True

    # --- ARCH-11.5 Step 2: resilience -------------------------------------
    LLM_REQUEST_DEADLINE_SECONDS: float = 25.0
    LLM_MAX_ATTEMPTS: int = 3
    LLM_BACKOFF_BASE_SECONDS: float = 0.5
    LLM_BACKOFF_CAP_SECONDS: float = 4.0
    LLM_BREAKER_THRESHOLD: int = 5
    LLM_BREAKER_RESET_SECONDS: float = 30.0
    LLM_FAILOVER_ENABLED: bool = False
    LLM_FALLBACK_PROVIDER: Optional[str] = None

    # --- ARCH-11.5 Step 3: vocabulary -------------------------------------
    VOCABULARY_MAX_TERMS: int = 400
    VOCABULARY_CACHE_TTL_SECONDS: float = 900.0
    VOCABULARY_CACHE_MAX_WORKSPACES: int = 32

    # --- ARCH-11.5 Step 4: intent -----------------------------------------
    INTENT_DETECTION_ENABLED: bool = True
    INTENT_BOOST_ENABLED: bool = True

    # --- ARCH-11.5 Step 5: citations --------------------------------------
    MAX_CITATIONS: int = 5
    SNIPPET_MAX_LENGTH: int = 300

    # ---- ARCH-12: streaming, budgeting, and generation limits ----------
    #
    # LLM_CONTEXT_WINDOW_TOKENS is the denominator for the 60/30/10
    # allocation in context_budget.py. It is deliberately conservative
    # against the smallest model in the supported set rather than the
    # largest: over-allocating produces a provider-side truncation that
    # silently drops whichever component the provider happens to cut, which
    # is precisely the invisible degradation A3 was about.
    LLM_CONTEXT_WINDOW_TOKENS: int = 32_768

    # A stream that has not finished in this long is not going to. Also the
    # cutoff sweep_in_flight() uses to find rows stranded in STREAMING.
    STREAM_DEADLINE_SECONDS: float = 120.0

    # ARCH-12 Step 2. A monthly spend ceiling is a budget, not a rate limit.
    STREAM_MAX_CONCURRENT_PER_USER: int = 2
    STREAM_MAX_CONCURRENT_PER_ORG: int = 20
    STREAM_MAX_MESSAGES_PER_MINUTE_PER_CONVERSATION: int = 10

    # ARCH-12 Step 5 (F7). Digital PDFs get line boxes from the text layer
    # rather than an empty block list. Set False only to reproduce
    # pre-ARCH-12 extraction while debugging a specific document.
    PDF_TEXT_LAYER_BBOXES_ENABLED: bool = True

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
    def _resolve_encryption_keys(self) -> "Settings":
        if os.environ.get("EMAIL_ENCRYPTION_KEY") is not None:
            raise ValueError(
                "EMAIL_ENCRYPTION_KEY was removed in ARCH-08 Step 1. Set "
                "EMAIL_ENCRYPTION_KEYS (plural, comma-separated, newest "
                "first). See docs/runbooks/arch07-key-rotation.md."
            )

        raw: Optional[str] = (
            self.EMAIL_ENCRYPTION_KEYS.get_secret_value()
            if self.EMAIL_ENCRYPTION_KEYS is not None
            else None
        )

        if not raw:
            if self.ENVIRONMENT not in _KEYLESS_ENVIRONMENTS:
                raise ValueError(
                    "EMAIL_ENCRYPTION_KEYS is required. Generate one with: "
                    'python -c "from cryptography.fernet import Fernet; '
                    'print(Fernet.generate_key().decode())"'
                )
            object.__setattr__(self, "_encryption_key_list", [])
            return self

        keys = [part.strip() for part in raw.split(",") if part.strip()]
        if not keys:
            raise ValueError("EMAIL_ENCRYPTION_KEYS resolved to an empty list")

        for index, key in enumerate(keys):
            try:
                Fernet(key.encode())
            except Exception as exc:
                raise ValueError(
                    f"EMAIL_ENCRYPTION_KEYS[{index}] is not a valid Fernet key "
                    f"(expected 32 url-safe base64-encoded bytes)."
                ) from exc

        if len(set(keys)) != len(keys):
            raise ValueError(
                "EMAIL_ENCRYPTION_KEYS contains duplicates."
            )

        object.__setattr__(self, "_encryption_key_list", keys)
        return self

    @property
    def encryption_key_list(self) -> list[str]:
        return getattr(self, "_encryption_key_list", [])

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