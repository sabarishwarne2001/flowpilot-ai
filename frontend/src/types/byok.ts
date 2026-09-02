/**
 * BYOK (Bring Your Own Key) types — ARCH-22, extended by ARCH-23.
 */

export type BYOKProvider =
  | "GROQ"
  | "GEMINI"
  | "OPENAI"
  | "ANTHROPIC"
  | "AZURE_OPENAI"
  | "MISTRAL";

export type BYOKTaskType =
  | "ASSISTANT"
  | "EXTRACTION"
  | "SUMMARY"
  | "VERIFICATION"
  | "EMBEDDING";

export type CredentialStatus =
  | "ACTIVE"
  | "INVALID"
  | "UNVALIDATED"
  | "UNCONFIGURED"
  | "UNROUTABLE";

export interface ProviderCatalogEntry {
  readonly provider: BYOKProvider;
  readonly label: string;
  readonly is_routable: boolean;
  readonly unroutable_reason: string | null;
  readonly key_prefix: string | null;
  readonly platform_key_available: boolean;
  readonly suggested_models: readonly string[];
  readonly requires_endpoint: boolean;
  readonly endpoint_suffix: string | null;
  readonly supported_tasks: readonly BYOKTaskType[];
}

export interface ProviderCredentialUpsert {
  readonly provider: BYOKProvider;
  readonly api_key: string;
  readonly allow_platform_fallback?: boolean;
  readonly resource_endpoint?: string | undefined;
  readonly deployment_name?: string | undefined;
}

export interface FallbackPolicyUpdate {
  readonly allow_platform_fallback: boolean;
}

export interface ProviderCredentialResponse {
  readonly id: string;
  readonly provider: BYOKProvider;
  readonly status: CredentialStatus;
  readonly is_routable: boolean;
  readonly unroutable_reason: string | null;
  readonly key_version: number;
  readonly key_fingerprint: string;
  readonly key_last_four: string;
  readonly allow_platform_fallback: boolean;
  readonly resource_endpoint: string | null;
  readonly deployment_name: string | null;
  readonly is_shape_complete: boolean;
  readonly fallback_is_possible: boolean;
  readonly last_validated_at: string | null;
  readonly last_validation_latency_ms: number | null;
  readonly validation_error: string | null;
  readonly last_used_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CredentialValidationResponse {
  readonly provider: BYOKProvider;
  readonly ok: boolean;
  readonly latency_ms: number;
  readonly error: string | null;
  readonly checked_at: string;
  readonly credential: ProviderCredentialResponse;
}

export interface ModelRouteUpsert {
  readonly task_type: BYOKTaskType;
  readonly provider: BYOKProvider;
  readonly model_name: string;
  readonly use_tenant_key: boolean;
  readonly is_enabled: boolean;
}

export interface ModelRouteResponse {
  readonly id: string;
  readonly task_type: BYOKTaskType;
  readonly task_label: string;
  readonly provider: BYOKProvider;
  readonly model_name: string;
  readonly use_tenant_key: boolean;
  readonly is_enabled: boolean;
  readonly effective_tenant_key: boolean;
  readonly downgrade_reason: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface TaskCatalogEntry {
  readonly task_type: BYOKTaskType;
  readonly label: string;
  readonly eligible_providers: readonly BYOKProvider[];
}

export interface BYOKSavingsResponse {
  readonly window_days: number;
  readonly byok_events: number;
  readonly platform_events: number;
  readonly byok_tokens: number;
  readonly platform_cost_micros: number;
  readonly byok_share_percent: number;
}

export interface BYOKOverviewResponse {
  readonly organization_id: string;
  readonly providers: readonly ProviderCatalogEntry[];
  readonly tasks: readonly TaskCatalogEntry[];
  readonly credentials: readonly ProviderCredentialResponse[];
  readonly routes: readonly ModelRouteResponse[];
  readonly savings: BYOKSavingsResponse;
  readonly routable_provider_count: number;
  readonly active_credential_count: number;
}

/* ============================================================================
 * Backward-Compatible Aliases for Existing Callers
 * ========================================================================== */

export type BYOKOverview = BYOKOverviewResponse;
export type BYOKSavings = BYOKSavingsResponse;
export type ProviderCredential = ProviderCredentialResponse;
export type CredentialValidation = CredentialValidationResponse;
export type ModelRoute = ModelRouteResponse;
export type CredentialUpsertRequest = ProviderCredentialUpsert;
export type FallbackPolicyRequest = FallbackPolicyUpdate;
export type ModelRouteUpsertRequest = ModelRouteUpsert;

/* ============================================================================
 * Presentation Helpers & Constants
 * ========================================================================== */

export const PROVIDER_LABELS: Readonly<Record<BYOKProvider, string>> = {
  GROQ: "Groq",
  GEMINI: "Google Gemini",
  OPENAI: "OpenAI",
  ANTHROPIC: "Anthropic",
  AZURE_OPENAI: "Azure OpenAI",
  MISTRAL: "Mistral",
};

export const TASK_LABELS: Readonly<Record<BYOKTaskType, string>> = {
  ASSISTANT: "Chat & assistant",
  EXTRACTION: "Document extraction",
  SUMMARY: "Summarization",
  VERIFICATION: "Verification",
  EMBEDDING: "Embeddings",
};

export const STATUS_LABELS: Readonly<Record<CredentialStatus, string>> = {
  ACTIVE: "Active",
  INVALID: "Invalid",
  UNVALIDATED: "Not yet tested",
  UNCONFIGURED: "Not configured",
  UNROUTABLE: "Stored, not routed",
};

export const STATUS_CLASSES: Readonly<Record<CredentialStatus, string>> = {
  ACTIVE: "bg-emerald-100 text-emerald-800 border-emerald-200",
  INVALID: "bg-red-100 text-red-800 border-red-200",
  UNVALIDATED: "bg-slate-100 text-slate-700 border-slate-200",
  UNCONFIGURED: "bg-slate-100 text-slate-500 border-slate-200",
  UNROUTABLE: "bg-amber-100 text-amber-900 border-amber-200",
};

export const TASK_ORDER: readonly BYOKTaskType[] = [
  "ASSISTANT",
  "EXTRACTION",
  "SUMMARY",
  "VERIFICATION",
  "EMBEDDING",
];

export const DOWNGRADE_EXPLANATIONS: Record<string, string> = {
  no_tenant_credential_configured:
    "No key is stored for this provider, so this task runs on the FlowPilot account.",
  tenant_credential_last_validation_failed:
    "The stored key failed its last test, so this task runs on the FlowPilot account until it is fixed.",
  route_rule_requests_platform_key:
    "This rule is set to use the FlowPilot account by design.",
  route_rule_disabled: "This rule is switched off.",
  no_route_rule_configured: "No rule is set; the workspace default applies.",
};

export const explainDowngrade = (reason: string | null): string | null => {
  if (!reason) {
    return null;
  }
  const known = DOWNGRADE_EXPLANATIONS[reason];
  if (known) {
    return known;
  }
  if (reason.startsWith("provider_unroutable")) {
    return "This provider cannot run on a tenant key in this release, so the task runs on the FlowPilot account.";
  }
  return reason;
};

export const credentialFor = (
  credentials: readonly ProviderCredentialResponse[],
  provider: BYOKProvider,
): ProviderCredentialResponse | undefined =>
  credentials.find((credential) => credential.provider === provider);

export const routeFor = (
  routes: readonly ModelRouteResponse[],
  taskType: BYOKTaskType,
): ModelRouteResponse | undefined =>
  routes.find((route) => route.task_type === taskType);

export const formatLatency = (ms: number | null): string =>
  ms === null || ms === undefined ? "—" : `${ms} ms`;

export const formatMicros = (micros: number): string =>
  `$${(micros / 1_000_000).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
