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
    PROJECT_NAME: str = "FlowPilot AI"
    APP_VERSION: str = APP_VERSION
    API_TITLE: str = "FlowPilot AI Core API"
    ENVIRONMENT: str = "development"
    API_V1_STR: str = "/api/v1"
    
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    LOG_LEVEL: str = "INFO"

    CORS_ORIGINS: str = "http://localhost:3000"

    POSTGRES_USER: str = "postgres"
    POSTGRES_PASSWORD: str = "postgres"
    POSTGRES_DB: str = "flowpilot"
    POSTGRES_HOST: str = "localhost"
    POSTGRES_PORT: int = 5432

    JWT_SECRET_KEY: SecretStr
    JWT_ALGORITHM: str = "HS256"

    @field_validator("JWT_SECRET_KEY")
    @classmethod
    def validate_jwt_secret(cls, v: SecretStr) -> SecretStr:
        """Reject compromised, absent, or trivially weak signing keys.

        LEAKED_JWT_SECRET_KEYS existed before this validator did, but nothing
        in the application consulted it. Meanwhile docker-compose.yml's
        x-app-env anchor supplies one of those exact keys as its default, so
        `docker compose up` with no JWT_SECRET_KEY exported boots on the
        documented-compromised key. This closes that.
        """
        secret = v.get_secret_value() if v is not None else ""

        if not secret:
            raise ValueError(
                "JWT_SECRET_KEY is required and has no default. Generate one "
                "per environment with: openssl rand -hex 32"
            )

        if secret in LEAKED_JWT_SECRET_KEYS:
            raise ValueError(
                "JWT_SECRET_KEY is a known-compromised key published in this "
                "repository's history. It is permanently rejected. Generate "
                "a fresh one with: openssl rand -hex 32\n"
                "If you reached this via `docker compose up`, export "
                "JWT_SECRET_KEY before starting -- compose falls back to "
                "the leaked value."
            )

        if len(secret) < 32:
            raise ValueError(
                f"JWT_SECRET_KEY is {len(secret)} characters. A minimum of "
                "32 is required; 64 hex characters (openssl rand -hex 32) "
                "is the documented choice."
            )

        return v

    API_KEY_PEPPER: SecretStr = SecretStr("flowpilot_default_api_key_pepper_secret_2026")
    REDIS_IDENTITY_PEPPER: SecretStr = SecretStr("flowpilot_default_redis_identity_pepper_2026")

    ACCESS_TOKEN_EXPIRE_MINUTES: int = 10
    REFRESH_TOKEN_EXPIRE_DAYS: int = 14
    SESSION_REUSE_GRACE_SECONDS: int = 10
    SESSION_CHAIN_WALK_LIMIT: int = 16

    EMAIL_VERIFICATION_TTL_HOURS: int = 24
    PASSWORD_RESET_TTL_MINUTES: int = 60
    IDENTITY_TOKEN_MAX_PER_WINDOW: int = 5
    IDENTITY_TOKEN_WINDOW_MINUTES: int = 60

    INVITATION_TTL_HOURS: int = 72
    INVITATION_RESEND_COOLDOWN_MINUTES: int = 5
    INVITATION_MAX_GRANTS: int = 50
    INVITATION_RETENTION_DAYS: int = 180

    OWNERSHIP_TRANSFER_TTL_DAYS: int = 7

    STORAGE_BACKEND: str = "local"
    UPLOAD_DIR: Path = Path("uploads")
    STORAGE_QUARANTINE_DIR: Path = Path("uploads/quarantine")
    AUDIT_SWEEPER_DATABASE_URL: Optional[SecretStr] = None
    FILE_RECLAMATION_DAYS: int = 30

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

    GROQ_API_KEY: SecretStr | None = None
    GEMINI_API_KEY: SecretStr | None = None
    LLM_PROVIDER: str = "groq"
    GROQ_MODEL_NAME: str = "llama-3.3-70b-versatile"
    GEMINI_MODEL_NAME: str = "gemini-3.5-flash"

    EMBEDDING_MODEL_NAME: str = "all-MiniLM-L6-v2"
    EMBEDDING_BATCH_SIZE: int = 32
    OCR_LANGUAGE: str = "en"

    PLATFORM_SMTP_HOST: str = ""
    PLATFORM_SMTP_PORT: int = 587
    PLATFORM_SMTP_USERNAME: str = ""
    PLATFORM_SMTP_PASSWORD: SecretStr | None = None
    PLATFORM_SMTP_ENCRYPTION: str = "TLS"
    PLATFORM_SMTP_FROM_EMAIL: str = "noreply@flowpilot.ai"
    PLATFORM_SMTP_FROM_NAME: str = "FlowPilot AI"

    FRONTEND_URL: str = "http://localhost:3000"
    EMAIL_ENCRYPTION_KEYS: Optional[SecretStr] = SecretStr("v3-Q90I2S6bXpL9_L3_0V8gJ0Z1P8yL1_L3_0V8gJ0Z=")

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

    MAX_DOCUMENT_PAGES: int = 500
    SCRUB_UPLOAD_METADATA: bool = True
    OCR_PROVIDER: str = "paddleocr"
    OCR_MAX_PAGES_PER_DOCUMENT: int = 500
    OCR_JOB_MAX_ATTEMPTS: int = 5
    OCR_GUARD_BEFORE_EXTRACTION: bool = False

    EMBEDDING_METERING_ENABLED: bool = True
    EMBEDDING_MAX_SEQUENCE_TOKENS: Optional[int] = None
    EMBEDDING_DIMENSION: int = 384
    GOLDEN_SET_PATH: str = "evaluation/golden/arch11_golden_v1.json"
    RETRIEVAL_BASELINE_DIR: str = "evaluation/baselines"

    DOCUMENT_CHUNK_PARTITIONS: int = 16
    HNSW_ITERATIVE_SCAN: str = "relaxed_order"
    HNSW_EF_SEARCH: int = 40
    APPLY_HNSW_SESSION_DEFAULTS: bool = True
    CHUNK_SIZE_TOKENS: int = 220
    CHUNK_OVERLAP_PCT: int = 10
    SPEND_PLATFORM_MONTHLY_BACKFILL_TOKENS: Optional[int] = 200_000_000

    LEXICAL_TSVECTOR_CONFIG: str = "english"
    LEXICAL_TRIGRAM_THRESHOLD: float = 0.3
    LEXICAL_CANDIDATES: int = 50
    HYBRID_CANDIDATES: int = 150
    HYBRID_WEIGHT_DENSE: float = 1.0
    HYBRID_WEIGHT_FULL_TEXT: float = 1.0
    HYBRID_WEIGHT_FUZZY: float = 0.5

    RERANKER_ENABLED: bool = True
    RERANKER_URL: str = "http://reranker:8081"
    RERANKER_INTERNAL_TOKEN: Optional[SecretStr] = None
    RERANKER_TIMEOUT: float = 2.0
    RERANKER_CONNECT_TIMEOUT: float = 0.5
    RERANKER_BREAKER_THRESHOLD: int = 5
    RERANKER_BREAKER_RESET_SECONDS: float = 30.0

    CONTEXT_INJECTION_BLOCK_THRESHOLD: int = 3
    LLM_METERING_ENABLED: bool = True
    LLM_REQUEST_DEADLINE_SECONDS: float = 25.0
    LLM_MAX_ATTEMPTS: int = 3
    LLM_BACKOFF_BASE_SECONDS: float = 0.5
    LLM_BACKOFF_CAP_SECONDS: float = 4.0
    LLM_BREAKER_THRESHOLD: int = 5
    LLM_BREAKER_RESET_SECONDS: float = 30.0
    LLM_FAILOVER_ENABLED: bool = False
    LLM_FALLBACK_PROVIDER: Optional[str] = None

    VOCABULARY_MAX_TERMS: int = 400
    VOCABULARY_CACHE_TTL_SECONDS: float = 900.0
    VOCABULARY_CACHE_MAX_WORKSPACES: int = 32
    INTENT_DETECTION_ENABLED: bool = True
    INTENT_BOOST_ENABLED: bool = True
    MAX_CITATIONS: int = 5
    SNIPPET_MAX_LENGTH: int = 300

    LLM_CONTEXT_WINDOW_TOKENS: int = 32_768
    STREAM_DEADLINE_SECONDS: float = 120.0
    STREAM_MAX_CONCURRENT_PER_USER: int = 2
    STREAM_MAX_CONCURRENT_PER_ORG: int = 20
    STREAM_MAX_MESSAGES_PER_MINUTE_PER_CONVERSATION: int = 10
    PDF_TEXT_LAYER_BBOXES_ENABLED: bool = True

    # ---- ARCH-14: pricing -------------------------------------------------
    PRICE_BOOK_CACHE_TTL_SECONDS: float = 300.0
    ROLLUP_SEAL_GRACE_HOURS: int = 26
    ROLLUP_BATCH_SIZE: int = 2_000
    ROLLUP_MAX_BATCHES: int = 20
    SPEND_USE_ROLLUP_READS: bool = True
    QUOTA_TIER_CACHE_TTL_SECONDS: float = 300.0
    QUOTA_DEFAULT_TIER_KEY: Optional[str] = None

    # ---- ARCH-14 Step 5: provider reconciliation --------------------------
    RECONCILE_MIN_AGE_DAYS: int = 2
    RECONCILE_BOUNDARY_HOURS: int = 6
    RECONCILE_ALERT_BPS: int = 50

    # ---- ARCH-18: COGS, unit economics & supplier reconciliation ----------
    # Variance beyond this fraction of modelled cost needs a human. Defaults
    # to RECONCILE_ALERT_BPS when unset so a platform tuned once for ARCH-14
    # does not need tuning twice for the same question.
    COGS_VARIANCE_ALERT_BPS: Optional[int] = None
    # A period is not reconciled until it has been closed this long. Suppliers
    # issue corrections; a variance against a period still being written to is
    # noise that pages someone at 3am.
    COGS_INVOICE_MIN_AGE_DAYS: Optional[int] = None
    # Default reporting window for the margins dashboard, in days.
    COGS_DEFAULT_WINDOW_DAYS: int = 30
    # Hard ceiling on rows a tenant-economics query will return.
    COGS_TENANT_RANKING_MAX: int = 500

    # ---- ARCH-14 Step 14.6: Gemini billing labels -------------------------
    GEMINI_BILLING_LABELS_ENABLED: bool = False
    GEMINI_USE_VERTEX: bool = False
    GEMINI_VERTEX_PROJECT: Optional[str] = None
    GEMINI_VERTEX_LOCATION: str = "us-central1"

    # ---- ARCH-13: automation engine ---------------------------------------
    AUTOMATION_MAX_DEPTH: int = 5
    AUTOMATION_MAX_NODES: int = 50
    AUTOMATION_EXECUTION_TIMEOUT_S: int = 120
    AUTOMATION_MAX_ACTIONS_PER_EXECUTION: int = 20
    AUTOMATION_DEFAULT_BUDGET_MICROS: int = 50_000
    AUTOMATION_ENGINE_ENABLED: bool = True

    # ---- ARCH-13 Step 13.7/13.8: verification ------------------------------
    AUTOMATION_VERIFICATION_AGENTS: int = 2
    AUTOMATION_AUTO_APPROVE_THRESHOLD: float = 0.85

    # ---- SEC-1: password hashing ------------------------------------------
    ARGON2_MEMORY_COST: int = 19456   # KiB -> 19 MiB
    ARGON2_TIME_COST: int = 2
    ARGON2_PARALLELISM: int = 1

    #: Optional floor on how long a login attempt takes, in milliseconds.
    AUTH_LOGIN_MIN_DURATION_MS: int = 0

    @field_validator("ARGON2_MEMORY_COST")
    @classmethod
    def validate_argon2_memory(cls, v: int) -> int:
        if v < 8192:
            raise ValueError(
                "ARGON2_MEMORY_COST below 8192 KiB (8 MiB) is beneath every "
                "current recommendation and is not worth the migration."
            )
        if v > 131072:
            raise ValueError(
                "ARGON2_MEMORY_COST above 131072 KiB (128 MiB) will OOM a "
                "1-2 GB API container under concurrent logins. If this "
                "deployment genuinely has the memory, raise this ceiling "
                "deliberately rather than passing through it by accident."
            )
        return v

    @field_validator("ARGON2_TIME_COST")
    @classmethod
    def validate_argon2_time(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ARGON2_TIME_COST must be at least 1.")
        if v > 10:
            raise ValueError(
                "ARGON2_TIME_COST above 10 puts password verification into "
                "the seconds range and will exhaust the request worker pool."
            )
        return v

    @field_validator("ARGON2_PARALLELISM")
    @classmethod
    def validate_argon2_parallelism(cls, v: int) -> int:
        if v < 1:
            raise ValueError("ARGON2_PARALLELISM must be at least 1.")
        if v > 4:
            raise ValueError(
                "ARGON2_PARALLELISM above 4 on a 1-2 vCPU container means "
                "threads contending for cores that do not exist."
            )
        return v

    # ---- SEC-1 Tranche 3: login failure accounting ------------------------
    LOGIN_BACKOFF_ENABLED: bool = True

    # ---- ARCH-15: Stripe transport ----------------------------------------
    STRIPE_SECRET_KEY: SecretStr | None = None
    STRIPE_PUBLISHABLE_KEY: str | None = None

    STRIPE_WEBHOOK_SECRETS: SecretStr | None = None
    STRIPE_WEBHOOK_TOLERANCE_SECONDS: int = 300
    STRIPE_API_VERSION: str = "2026-07-29.dahlia"
    STRIPE_LIVEMODE: bool = False

    STRIPE_MAX_NETWORK_RETRIES: int = 2
    STRIPE_TIMEOUT_SECONDS: float = 20.0
    STRIPE_MAX_WEBHOOK_BODY_BYTES: int = 512 * 1024
    STRIPE_INBOUND_BATCH_SIZE: int = 25
    STRIPE_INBOUND_LEASE_SECONDS: int = 60
    STRIPE_INBOUND_MAX_ATTEMPTS: int = 8

    # ---- ARCH-15: billing policy ------------------------------------------
    BILLING_DEFAULT_CURRENCY: str = "USD"
    BILLING_DEFAULT_QUOTA_TIER_KEY: str | None = None
    BILLING_SEAT_PRICE_LOOKUP_KEY: str | None = None
    BILLING_SEAT_SYNC_ENABLED: bool = True
    BILLING_SEAT_PRORATION_BEHAVIOR: str = "create_prorations"

    # ---- ARCH-15 Tranche 3: invoices --------------------------------------
    BILLING_INVOICE_NUMBER_PREFIX: str = "FP"
    BILLING_SEAT_EVENT_TYPE: str = "billing.seat"
    BILLING_SEAT_FALLBACK_PRICE_MICROS: int = 0
    BILLING_STRIPE_TOTAL_TOLERANCE_MICROS: int = 1

    # ---- ARCH-15 Tranche 4: portal, checkout, dunning ---------------------
    BILLING_REAUTH_WINDOW_S: int = 300
    BILLING_REAUTH_REQUIRED: bool = True
    BILLING_PORTAL_RETURN_URL: str | None = None
    BILLING_CHECKOUT_SUCCESS_URL: str | None = None
    BILLING_CHECKOUT_CANCEL_URL: str | None = None
    BILLING_SEAT_PRICE_ID: str | None = None
    BILLING_DUNNING_MAX_STEP: str = "NOTIFY_3"

    @field_validator("BILLING_DUNNING_MAX_STEP")
    @classmethod
    def validate_dunning_max_step(cls, v: str) -> str:
        allowed = {
            "NOTIFY_1",
            "NOTIFY_2",
            "NOTIFY_3",
            "RESTRICT_WRITES",
            "SUSPEND_WRITES",
        }
        normalized = (v or "").strip().upper()
        if normalized not in allowed:
            raise ValueError(
                f"BILLING_DUNNING_MAX_STEP must be one of {sorted(allowed)}."
            )
        return normalized

    @field_validator("BILLING_REAUTH_WINDOW_S")
    @classmethod
    def validate_reauth_window(cls, v: int) -> int:
        if v < 30:
            raise ValueError(
                "BILLING_REAUTH_WINDOW_S below 30s makes the portal "
                "unusable — the redirect itself takes longer than that."
            )
        if v > 3600:
            raise ValueError(
                "BILLING_REAUTH_WINDOW_S above one hour defeats the purpose "
                "of F6: a tab left open should not be able to change a card."
            )
        return v

    @field_validator("BILLING_SEAT_PRORATION_BEHAVIOR")
    @classmethod
    def validate_proration_behavior(cls, v: str) -> str:
        allowed = {"create_prorations", "none", "always_invoice"}
        normalized = v.strip().lower()
        if normalized not in allowed:
            raise ValueError(
                "BILLING_SEAT_PRORATION_BEHAVIOR must be one of "
                f"{sorted(allowed)} — these are Stripe's own values."
            )
        return normalized

    @field_validator("STRIPE_API_VERSION")
    @classmethod
    def validate_stripe_api_version(cls, v: str) -> str:
        cleaned = v.strip()
        if not cleaned:
            raise ValueError(
                "STRIPE_API_VERSION must be pinned explicitly. Leaving it to "
                "the account default means a dashboard-side version bump "
                "changes payload shapes without a deploy."
            )
        return cleaned

    @field_validator("BILLING_DEFAULT_CURRENCY")
    @classmethod
    def validate_billing_currency(cls, v: str) -> str:
        cleaned = v.strip().upper()
        if len(cleaned) != 3:
            raise ValueError(
                "BILLING_DEFAULT_CURRENCY must be a 3-letter ISO-4217 code."
            )
        return cleaned

    @field_validator("AUTOMATION_MAX_DEPTH")
    @classmethod
    def validate_automation_max_depth(cls, v: int) -> int:
        if v < 1:
            raise ValueError("AUTOMATION_MAX_DEPTH must be at least 1.")
        if v > 16:
            raise ValueError(
                "AUTOMATION_MAX_DEPTH must not exceed 16, the ck_outbox_events"
                "_depth_bounded database ceiling."
            )
        return v

    @field_validator("AUTOMATION_AUTO_APPROVE_THRESHOLD")
    @classmethod
    def validate_auto_approve_threshold(cls, v: float) -> float:
        if not (0.0 < v <= 1.0):
            raise ValueError(
                "AUTOMATION_AUTO_APPROVE_THRESHOLD must be in (0.0, 1.0]."
            )
        return v

    @field_validator("AUTOMATION_VERIFICATION_AGENTS")
    @classmethod
    def validate_verification_agents(cls, v: int) -> int:
        if v < 2:
            raise ValueError(
                "AUTOMATION_VERIFICATION_AGENTS must be at least 2."
            )
        if v > 5:
            raise ValueError(
                "AUTOMATION_VERIFICATION_AGENTS above 5 multiplies enrichment spend by >5x."
            )
        return v

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
    def stripe_webhook_secret_list(self) -> list[str]:
        raw = (
            self.STRIPE_WEBHOOK_SECRETS.get_secret_value()
            if self.STRIPE_WEBHOOK_SECRETS is not None
            else None
        )
        if not raw:
            return []
        return [part.strip() for part in raw.split(",") if part.strip()]

    @model_validator(mode="after")
    def _assert_stripe_mode_matches_key(self) -> "Settings":
        if self.ENVIRONMENT in _KEYLESS_ENVIRONMENTS:
            return self

        secret = (
            self.STRIPE_SECRET_KEY.get_secret_value()
            if self.STRIPE_SECRET_KEY is not None
            else None
        )
        if not secret:
            return self

        looks_live = secret.startswith("sk_live_") or secret.startswith("rk_live_")
        looks_test = secret.startswith("sk_test_") or secret.startswith("rk_test_")

        if self.STRIPE_LIVEMODE and looks_test:
            raise ValueError(
                "STRIPE_LIVEMODE is true but STRIPE_SECRET_KEY is a test-mode "
                "key. Every inbound live event would be refused as a mode "
                "mismatch while the service reported healthy."
            )
        if not self.STRIPE_LIVEMODE and looks_live:
            raise ValueError(
                "STRIPE_SECRET_KEY is a live-mode key but STRIPE_LIVEMODE is "
                "false. This configuration charges real cards from a "
                "deployment that believes it is in test mode."
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