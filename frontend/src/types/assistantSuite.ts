/**
 * ARCH-39 — conversation sessions, scope, models and prompt templates.
 * Mirrors app/schemas/assistant_suite.py.
 */

export type ConversationKind = "workspace" | "document";
export type ConversationKindFilter = "all" | ConversationKind;
export type ConversationScopeMode = "WORKSPACE" | "SELECTED" | "DOCUMENT";

export interface ConversationSession {
  readonly id: string;
  readonly title: string;
  readonly kind: ConversationKind;
  readonly scope_mode: ConversationScopeMode;
  readonly work_item_id: string | null;
  readonly document_title: string | null;
  readonly scope_document_count: number;
  readonly model_override: string | null;
  readonly pinned: boolean;
  readonly archived: boolean;
  readonly message_count: number;
  readonly created_at: string;
  readonly last_message_at: string | null;
}

export interface ConversationSessionFilters {
  readonly kind: ConversationKindFilter;
  readonly archived: boolean;
  readonly q: string;
}

export interface ConversationSessionUpdate {
  readonly title?: string;
  readonly pinned?: boolean;
  readonly archived?: boolean;
  readonly model_override?: string;
  readonly clear_model_override?: boolean;
}

export interface ConversationScope {
  readonly mode: ConversationScopeMode;
  readonly work_item_ids: readonly string[];
  readonly max_documents: number;
}

export interface ConversationScopeUpdate {
  readonly mode: "WORKSPACE" | "SELECTED";
  readonly work_item_ids: readonly string[];
}

export interface AssistantModelOption {
  readonly provider: string;
  readonly model: string;
  readonly is_workspace_default: boolean;
  readonly input_micros_per_million: number | null;
  readonly output_micros_per_million: number | null;
  readonly currency: string | null;
}

export interface PromptTemplate {
  readonly id: string;
  readonly name: string;
  readonly body: string;
  readonly created_by_user_id: string | null;
  readonly created_at: string;
  readonly updated_at: string;
}

export interface PromptTemplateWrite {
  readonly name: string;
  readonly body: string;
}

export type ExportFormat = "json" | "markdown";

/** "$0.15 / $0.60 per 1M tokens", or null when the model is unpriced. */
export const formatModelPrice = (option: AssistantModelOption): string | null => {
  if (option.input_micros_per_million === null || option.output_micros_per_million === null) {
    return null;
  }
  const currency = option.currency ?? "USD";
  const format = (micros: number): string =>
    new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
      maximumFractionDigits: 4,
    }).format(micros / 1_000_000);
  return `${format(option.input_micros_per_million)} in / ${format(
    option.output_micros_per_million,
  )} out per 1M tokens`;
};
