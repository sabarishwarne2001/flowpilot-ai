"""ARCH-22 / ARCH-23 — BYOK credential, routing and savings DTOs.

THE ONE RULE THIS FILE ENFORCES BY OMISSION
===========================================

No response model in this module has an `api_key` field, and none ever will.
A tenant sends a key exactly once, in `ProviderCredentialUpsert`. From then on
the console works with `key_fingerprint` and `key_last_four`, which are enough
to answer "is the key I pasted the key you are using?" and useless to anyone
who intercepts them.

`ProviderCredentialUpsert.api_key` is typed `SecretStr` so that a validation
error, a repr, or a logged request body renders it as `**********`. Pydantic
does that automatically; a plain `str` does not, and FastAPI logs request
bodies on 422.

WHAT ARCH-23 ADDED, AND WHY THE ENDPOINT IS DIFFERENT FROM THE KEY
==================================================================

Azure OpenAI needs `resource_endpoint` and `deployment_name` alongside the key.
Neither is secret — both appear in the Azure portal URL — so both are plain
`str` and both appear in the response model. That is deliberate: the console
has to show a tenant which resource they configured, and hiding a non-secret
behind the fingerprint treatment would make the field unusable while adding no
protection.

But `resource_endpoint` is a hostname **the server will connect to**, which
makes it the same class of input as a webhook target: an SSRF vector. ARCH-23
finding B2. It is constrained in four places, deliberately:

  1. Here, at write time — `_endpoint_is_safe` below.
  2. `azure_endpoint_suffix`, a database CHECK — catches writers that are not
     this API (admin tooling, a future bulk import, Alembic data migrations).
  3. `ProviderClientFactory._build_azure_openai` — catches a row that reached
     the executor without passing either of the above.
  4. `SSRFSafeHTTPClient` at probe time — resolves DNS and refuses private,
     link-local and loopback addresses, so a `*.openai.azure.com` name that
     someone has pointed at 169.254.169.254 still fails.

Four layers looks excessive for one field. It is not: layers 1–3 constrain the
*name* and only layer 4 constrains the *address*, and an attacker who controls
a DNS record controls the mapping between them.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Literal, Optional

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)

from app.core.byok_providers import (
    BYOK_PROVIDER_VALUES,
    BYOK_TASK_TYPE_VALUES,
    endpoint_suffix_for,
    normalize_provider,
    normalize_task_type,
    requires_endpoint,
    supports_task,
)
from app.core.encryption import MAX_PLAINTEXT_LENGTH

BYOKProvider = Literal[
    "GROQ", "GEMINI", "OPENAI", "ANTHROPIC", "AZURE_OPENAI", "MISTRAL"
]

BYOKTaskType = Literal[
    "ASSISTANT", "EXTRACTION", "SUMMARY", "VERIFICATION", "EMBEDDING"
]

CredentialStatus = Literal[
    "ACTIVE", "INVALID", "UNVALIDATED", "UNCONFIGURED", "UNROUTABLE"
]

#: A conservative hostname shape. Labels are alphanumeric with internal
#: hyphens, separated by dots. Deliberately narrower than RFC 1123 — it
#: excludes trailing dots, underscores and IDN punycode, none of which appear
#: in an Azure OpenAI resource name and all of which are parser-confusion
#: material when a string crosses from Python to a URL library to a resolver.
_HOSTNAME_RE = re.compile(
    r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)

MAX_ENDPOINT_LENGTH: int = 255
MAX_DEPLOYMENT_LENGTH: int = 128

#: Azure deployment names: letters, digits, hyphens and underscores. Azure
#: itself is stricter, but this is the set that cannot break a URL path.
_DEPLOYMENT_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")


def _normalise_endpoint(raw: str) -> str:
    """Strip scheme, path, port and case from a tenant-typed endpoint.

    Tenants paste what the Azure portal shows them, which is a full URL with a
    trailing slash. Storing that verbatim would mean the suffix check, the
    database CHECK and the client builder each have to re-parse it, and three
    parsers of the same string is how they end up disagreeing. One normal form,
    established once, at the boundary.
    """
    value = (raw or "").strip().lower()
    if value.startswith("https://"):
        value = value[len("https://"):]
    elif value.startswith("http://"):
        # Rejected below by the suffix check anyway, but stripping it here
        # produces a clearer error than "http://x.openai.azure.com is not a
        # *.openai.azure.com host".
        value = value[len("http://"):]
    value = value.split("/", 1)[0]
    value = value.split("?", 1)[0]
    # A port would make this a different origin than the one the suffix check
    # believes it approved.
    if ":" in value:
        value = value.split(":", 1)[0]
    return value.rstrip(".")


def _endpoint_is_safe(endpoint: str, *, provider: str) -> str:
    """Validate a tenant-supplied endpoint. ARCH-23 finding B2, layer 1."""
    host = _normalise_endpoint(endpoint)

    if not host:
        raise ValueError("The resource endpoint cannot be empty.")
    if len(host) > MAX_ENDPOINT_LENGTH:
        raise ValueError(
            f"The resource endpoint is longer than {MAX_ENDPOINT_LENGTH} "
            "characters."
        )
    if not _HOSTNAME_RE.match(host):
        raise ValueError(
            f"'{host}' is not a valid hostname. Provide the resource host, "
            "for example 'my-resource.openai.azure.com'."
        )

    suffix = endpoint_suffix_for(provider)
    if suffix and not host.endswith(suffix):
        raise ValueError(
            f"The resource endpoint must be a {suffix} host. FlowPilot's "
            f"server connects to this address, so it is restricted to the "
            f"provider's own domain — an arbitrary host here would let this "
            f"field reach internal services."
        )

    # `evil.com#.openai.azure.com` cannot reach here (the hostname regex
    # rejects '#'), but a bare-suffix host like `openai.azure.com` passes both
    # checks above and is not a tenant resource.
    if suffix and host == suffix.lstrip("."):
        raise ValueError(
            f"'{host}' is the provider's base domain, not a resource "
            "endpoint. Include your resource name, for example "
            f"'my-resource{suffix}'."
        )

    return host


# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------


class ProviderCatalogEntry(BaseModel):
    """One provider as the console should present it."""

    provider: BYOKProvider
    label: str
    is_routable: bool = Field(
        description=(
            "True when a tenant key here actually serves traffic. False means "
            "the credential is stored and can be validated, but every request "
            "still runs on the platform account — the console must say so "
            "rather than showing a green badge. As of ARCH-23 all six "
            "providers are routable; the field stays because the next "
            "provider added will not be."
        )
    )
    unroutable_reason: Optional[str] = None
    key_prefix: Optional[str] = None
    platform_key_available: bool = Field(
        description=(
            "True when FlowPilot holds its own key for this provider. When "
            "false, allow_platform_fallback cannot do anything even if set."
        )
    )
    suggested_models: list[str] = Field(default_factory=list)

    #: ARCH-23. True for Azure OpenAI only. The console renders two extra
    #: inputs when set, rather than testing `provider === "AZURE_OPENAI"` in
    #: TypeScript — the same reason the backend derives it from the registry.
    requires_endpoint: bool = False
    endpoint_suffix: Optional[str] = Field(
        default=None,
        description=(
            "The DNS suffix a resource endpoint must end with. Surfaced so "
            "the console can validate before a round trip and show the "
            "expected shape in the placeholder."
        ),
    )

    #: ARCH-23. Task types this provider can serve. Groq and Anthropic expose
    #: no embeddings API, so the console must not offer them for EMBEDDING.
    supported_tasks: list[BYOKTaskType] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------


class ProviderCredentialUpsert(BaseModel):
    """Create or rotate one provider credential."""

    provider: BYOKProvider
    api_key: SecretStr = Field(
        description="The provider API key. Stored encrypted; never returned."
    )
    allow_platform_fallback: Optional[bool] = Field(
        default=None,
        description=(
            "Omit to leave the existing policy untouched on a rotation. "
            "Rotating a key must not silently re-open a fallback the tenant "
            "had closed."
        ),
    )

    #: ARCH-23. Azure OpenAI only. Not secret; see the module docstring.
    resource_endpoint: Optional[str] = Field(
        default=None,
        max_length=MAX_ENDPOINT_LENGTH,
        description=(
            "Azure OpenAI resource host, e.g. 'my-resource.openai.azure.com'. "
            "Scheme, port and path are stripped. Required for AZURE_OPENAI, "
            "rejected for every other provider."
        ),
    )
    deployment_name: Optional[str] = Field(
        default=None,
        max_length=MAX_DEPLOYMENT_LENGTH,
        description=(
            "Azure OpenAI deployment name — your own label for a deployed "
            "model, not the model id. Required for AZURE_OPENAI."
        ),
    )

    @field_validator("api_key")
    @classmethod
    def _key_within_encryption_limit(cls, value: SecretStr) -> SecretStr:
        """Refuse at the boundary what `encrypt_password` would refuse later.

        `app/core/encryption.py` caps plaintext at MAX_PLAINTEXT_LENGTH. A key
        over that limit would be accepted here, travel through the service
        layer, and fail at the encryption call — by which point the error
        surfaces as a 500 rather than a 422 naming the field.
        """
        secret = value.get_secret_value()
        if not secret.strip():
            raise ValueError("The API key cannot be blank.")
        if len(secret) > MAX_PLAINTEXT_LENGTH:
            raise ValueError(
                f"The API key is longer than {MAX_PLAINTEXT_LENGTH} "
                "characters, which is more than any provider issues. Check "
                "for a pasted newline or an extra field."
            )
        return value

    @field_validator("deployment_name")
    @classmethod
    def _deployment_shape(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            return None
        if not _DEPLOYMENT_RE.match(cleaned):
            raise ValueError(
                "A deployment name may contain letters, digits, dots, hyphens "
                "and underscores only. It becomes part of a request URL."
            )
        return cleaned

    @model_validator(mode="after")
    def _endpoint_matches_provider(self) -> "ProviderCredentialUpsert":
        """Both directions, because both are wrong in different ways.

        Missing fields on Azure produce a credential that cannot be validated
        or executed — the database CHECK would reject it anyway, but a 422
        naming the field beats an IntegrityError.

        Fields present on a non-Azure provider are rejected rather than
        ignored. Silently dropping them would leave a tenant believing they
        had configured something. It is also the signal that the console is
        sending a shape the backend does not model, which is worth failing
        loudly on rather than absorbing.
        """
        provider = normalize_provider(self.provider)

        if requires_endpoint(provider):
            if not self.resource_endpoint:
                raise ValueError(
                    "Azure OpenAI needs a resource endpoint "
                    "(e.g. 'my-resource.openai.azure.com'). An API key alone "
                    "cannot be validated or used."
                )
            if not self.deployment_name:
                raise ValueError(
                    "Azure OpenAI needs a deployment name — your own label "
                    "for a deployed model, shown in Azure AI Studio."
                )
            object.__setattr__(
                self,
                "resource_endpoint",
                _endpoint_is_safe(self.resource_endpoint, provider=provider),
            )
            return self

        if self.resource_endpoint or self.deployment_name:
            raise ValueError(
                f"{provider} authenticates with an API key alone. "
                "resource_endpoint and deployment_name apply to Azure OpenAI "
                "only and were not saved."
            )
        return self


class FallbackPolicyUpdate(BaseModel):
    """Change whether a failed tenant call may reach the platform account."""

    allow_platform_fallback: bool


class ProviderCredentialResponse(BaseModel):
    """A stored credential, minus the credential."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    provider: BYOKProvider
    status: CredentialStatus
    is_routable: bool
    unroutable_reason: Optional[str] = None
    key_version: int
    key_fingerprint: str
    key_last_four: str
    allow_platform_fallback: bool

    #: ARCH-23. Returned in full — neither is secret, and the console has to
    #: show a tenant which Azure resource they pointed at.
    resource_endpoint: Optional[str] = None
    deployment_name: Optional[str] = None

    #: ARCH-23. False for an Azure row missing either field. Derived on the
    #: model (`TenantProviderCredential.is_shape_complete`) rather than
    #: recomputed here, so the console and the executor agree by construction.
    is_shape_complete: bool = True

    #: ARCH-23 B3. False for OPENAI, ANTHROPIC, AZURE_OPENAI and MISTRAL,
    #: where FlowPilot holds no key of its own. The console greys out the
    #: fallback toggle rather than letting a tenant enable a safety net that
    #: cannot exist.
    fallback_is_possible: bool = True

    last_validated_at: Optional[datetime] = None
    last_validation_latency_ms: Optional[int] = None
    validation_error: Optional[str] = None
    last_used_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class CredentialValidationResponse(BaseModel):
    """The result of a live "Test & Validate" round trip."""

    provider: BYOKProvider
    ok: bool
    latency_ms: int
    error: Optional[str] = None
    checked_at: datetime
    credential: ProviderCredentialResponse


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------


class ModelRouteUpsert(BaseModel):
    """Point one pipeline task at one provider and model."""

    task_type: BYOKTaskType
    provider: BYOKProvider
    model_name: str = Field(min_length=1, max_length=128)
    use_tenant_key: bool = True
    is_enabled: bool = True

    @field_validator("model_name")
    @classmethod
    def _model_is_trimmed(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("A routing rule needs a model name.")
        return cleaned

    @field_validator("provider")
    @classmethod
    def _provider_known(cls, value: str) -> str:
        key = normalize_provider(value)
        if key not in BYOK_PROVIDER_VALUES:
            raise ValueError(f"'{value}' is not a known provider.")
        return key

    @field_validator("task_type")
    @classmethod
    def _task_known(cls, value: str) -> str:
        key = normalize_task_type(value)
        if key not in BYOK_TASK_TYPE_VALUES:
            raise ValueError(f"'{value}' is not a known task type.")
        return key

    @model_validator(mode="after")
    def _provider_serves_this_task(self) -> "ModelRouteUpsert":
        """ARCH-23. Refuse a route the provider cannot serve.

        Groq and Anthropic expose no embeddings API. A rule pointing EMBEDDING
        at either would save cleanly and fail at execution — the worst shape
        for a policy control, because the failure surfaces in a document
        pipeline hours later and far from the setting that caused it.

        This is the same principle as the ARCH-22 refusal to accept a key for
        an unroutable provider: the console must not accept a configuration it
        knows cannot work.
        """
        provider = normalize_provider(self.provider)
        task = normalize_task_type(self.task_type)

        if not supports_task(provider, task):
            raise ValueError(
                f"{provider} does not serve {task} requests, so this rule "
                "would fail every time it ran. Choose a provider that "
                "supports this task."
            )
        return self


class ModelRouteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    task_type: BYOKTaskType
    task_label: str
    provider: BYOKProvider
    model_name: str
    use_tenant_key: bool
    is_enabled: bool

    #: True when the rule as saved will actually run on the tenant's key.
    #: Diverges from `use_tenant_key` when the credential was removed or its
    #: last validation failed after the rule was written.
    effective_tenant_key: bool
    downgrade_reason: Optional[str] = None

    created_at: datetime
    updated_at: datetime


class TaskCatalogEntry(BaseModel):
    task_type: BYOKTaskType
    label: str

    #: ARCH-23. Which providers can serve this task, so the console can filter
    #: the provider dropdown per task rather than offering all six and
    #: rejecting on submit.
    eligible_providers: list[BYOKProvider] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Savings
# ---------------------------------------------------------------------------


class BYOKSavingsResponse(BaseModel):
    """What BYOK has removed from FlowPilot's supplier bill.

    `platform_cost_micros` is the cost of events the platform DID pay for, and
    `byok_events` counts those it did not. There is deliberately no
    "estimated saving" figure derived by pricing BYOK tokens at platform
    rates: we do not know what the tenant's own contract charges them, and an
    invented number in a cost widget is the kind of thing that ends up in a
    board deck.
    """

    window_days: int
    byok_events: int
    platform_events: int
    byok_tokens: int
    platform_cost_micros: int
    byok_share_percent: float


# ---------------------------------------------------------------------------
# Overview
# ---------------------------------------------------------------------------


class BYOKOverviewResponse(BaseModel):
    """Everything the console needs for a first paint, in one round trip."""

    organization_id: uuid.UUID
    providers: list[ProviderCatalogEntry]
    tasks: list[TaskCatalogEntry]
    credentials: list[ProviderCredentialResponse]
    routes: list[ModelRouteResponse]
    savings: BYOKSavingsResponse
    routable_provider_count: int
    active_credential_count: int


__all__ = [
    "BYOKOverviewResponse",
    "BYOKProvider",
    "BYOKSavingsResponse",
    "BYOKTaskType",
    "CredentialStatus",
    "CredentialValidationResponse",
    "FallbackPolicyUpdate",
    "MAX_DEPLOYMENT_LENGTH",
    "MAX_ENDPOINT_LENGTH",
    "ModelRouteResponse",
    "ModelRouteUpsert",
    "ProviderCatalogEntry",
    "ProviderCredentialResponse",
    "ProviderCredentialUpsert",
    "TaskCatalogEntry",
]