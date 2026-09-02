/**
 * ARCH-22 — enterprise BYOK and per-tenant model routing.
 *
 * These mirror app/schemas/byok.py. Two absences are deliberate and must stay
 * absences: there is no `apiKey` on any response type and no `encryptedApiKey`
 * anywhere. A tenant sends a key once, over a PUT, and the console works with
 * `keyFingerprint` and `keyLastFour` from then on. Adding a key field here
 * would compile fine and quietly invite a component to render it.
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

/**
 * UNROUTABLE is not a failure state. It means the credential is valid and
 * stored but the execution layer will not use it, so the tenant's traffic is
 * still running on FlowPilot's provider account. Rendering it as ACTIVE would
 * make the console assert a compliance property that is not true.
 */
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
}

export interface ProviderCredential {
  readonly id: string;
  readonly provider: BYOKProvider;
  readonly status: CredentialStatus;
  readonly is_routable: boolean;
  readonly unroutable_reason: string | null;
  readonly key_version: number;
  readonly key_fingerprint: string;
  readonly key_last_four: string;
  readonly allow_platform_fallback: boolean;
  readonly last_validated_at: string | null;
  readonly last_validation_latency_ms: number | null;
  readonly validation_error: string | null;
  readonly last_used_at: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface CredentialValidation {
  readonly provider: BYOKProvider;
  readonly ok: boolean;
  readonly latency_ms: number;
  readonly error: string | null;
  readonly checked_at: string;
  readonly credential: ProviderCredential;
}

export interface ModelRoute {
  readonly id: string;
  readonly task_type: BYOKTaskType;
  readonly task_label: string;
  readonly provider: BYOKProvider;
  readonly model_name: string;
  /** What the tenant saved. */
  readonly use_tenant_key: boolean;
  readonly is_enabled: boolean;
  /** What the next request will actually do. Diverges when a credential was
   *  retired or failed validation after the rule was written. */
  readonly effective_tenant_key: boolean;
  readonly downgrade_reason: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface TaskCatalogEntry {
  readonly task_type: BYOKTaskType;
  readonly label: string;
}

export interface BYOKSavings {
  readonly window_days: number;
  readonly byok_events: number;
  readonly platform_events: number;
  readonly byok_tokens: number;
  readonly platform_cost_micros: number;
  readonly byok_share_percent: number;
}

export interface BYOKOverview {
  readonly organization_id: string;
  readonly providers: readonly ProviderCatalogEntry[];
  readonly tasks: readonly TaskCatalogEntry[];
  readonly credentials: readonly ProviderCredential[];
  readonly routes: readonly ModelRoute[];
  readonly savings: BYOKSavings;
  readonly routable_provider_count: number;
  readonly active_credential_count: number;
}

export interface CredentialUpsertRequest {
  readonly provider: BYOKProvider;
  readonly api_key: string;
  readonly allow_platform_fallback?: boolean;
}

export interface FallbackPolicyRequest {
  readonly allow_platform_fallback: boolean;
}

export interface ModelRouteUpsertRequest {
  readonly task_type: BYOKTaskType;
  readonly provider: BYOKProvider;
  readonly model_name: string;
  readonly use_tenant_key: boolean;
  readonly is_enabled?: boolean;
}

// ---------------------------------------------------------------------------
// Presentation helpers
// ---------------------------------------------------------------------------

export const STATUS_LABELS: Record<CredentialStatus, string> = {
  ACTIVE: "Active",
  INVALID: "Invalid",
  UNVALIDATED: "Not yet tested",
  UNCONFIGURED: "Not configured",
  UNROUTABLE: "Stored, not routed",
};

/**
 * Tailwind classes per badge. UNROUTABLE is amber rather than green or red: it
 * is neither a working BYOK route nor a broken key, and colouring it green is
 * exactly the misreading the status exists to prevent.
 */
export const STATUS_CLASSES: Record<CredentialStatus, string> = {
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

/** Human text for the machine-readable downgrade reasons the API returns. */
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
  credentials: readonly ProviderCredential[],
  provider: BYOKProvider,
): ProviderCredential | undefined =>
  credentials.find((credential) => credential.provider === provider);

export const routeFor = (
  routes: readonly ModelRoute[],
  taskType: BYOKTaskType,
): ModelRoute | undefined =>
  routes.find((route) => route.task_type === taskType);

export const formatLatency = (ms: number | null): string =>
  ms === null ? "—" : `${ms} ms`;

export const formatMicros = (micros: number): string =>
  `$${(micros / 1_000_000).toLocaleString(undefined, {
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  })}`;
