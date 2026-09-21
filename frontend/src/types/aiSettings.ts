/**
 * ARCH40-S2:ai-settings-types. The workspace AI defaults, and what resolves.
 *
 * `system_prompt_version`, `prompt_version` and `enable_token_tracking` are
 * gone: nothing in the product read them, and arch40_step3 drops the columns.
 * `input_cost_per_1k_tokens` / `output_cost_per_1k_tokens` went in ARCH-14,
 * when prices became platform-owned; the console kept sending them anyway.
 */

export type AIProvider =
    | "GROQ"
    | "GEMINI";

export interface AISettings {
    id: string;
    workspace_id: string;
    updated_by_user_id: string | null;

    provider: AIProvider;
    model: string;
    temperature: number;
    max_output_tokens: number;
    top_p: number;
    frequency_penalty: number;
    presence_penalty: number;
    enable_streaming: boolean;

    created_at: string;
    updated_at: string;
}

export type UpdateAISettingsRequest = Omit<
    AISettings,
    "id" | "workspace_id" | "updated_by_user_id" | "created_at" | "updated_at"
>;

/**
 * GET .../ai-settings/resolved. Read-only: what actually serves this
 * workspace. `tenant_model_routes` owns routing for a routed task and these
 * workspace defaults are the fallback; this is where the console says which
 * one won.
 */
export interface ResolvedAISettings {
    readonly workspace_id: string;
    readonly organization_id: string;
    readonly resolved_provider: string;
    readonly resolved_model: string;
    readonly resolution_origin: "route_rule" | "ai_settings_default" | string;
    readonly uses_tenant_key: boolean;
    readonly downgrade_reason: string | null;
    readonly currency: string | null;
    /** Micros per million input tokens. Null, never 0, when unpriced. */
    readonly price_per_1m_input_micros: number | null;
    readonly price_book_version: number | null;
    readonly price_is_fallback: boolean;
    readonly spend_limit_configured: boolean;
    readonly spend_limit_hard_stop: boolean;
    readonly spend_limit_max_cost_micros: number | null;
    readonly spend_limit_period: string | null;
    readonly byok_configured: boolean;
    readonly streaming_enabled: boolean;
    readonly warnings: readonly string[];
}
