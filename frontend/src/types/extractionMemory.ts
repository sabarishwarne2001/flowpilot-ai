/** ARCH41-S3:types — mirrors app/schemas/extraction_memory.py field for field. */

export type MemoryMode = "OFF" | "SHADOW" | "AUTO";
export type TemplateState = "LEARNING" | "TRIAL" | "ACTIVE" | "REJECTED";
export type RuleState = "SHADOW" | "ACTIVE" | "RETIRED";
export type TrialState = "RUNNING" | "PROMOTED" | "REJECTED" | "ABANDONED";

export interface MemorySettings {
  readonly mode: MemoryMode;
  readonly is_default: boolean;
  readonly updated_at?: string | null;
}

export interface MemoryPotential {
  readonly reviewed_documents: number;
  readonly corrected_fields: number;
}

export interface MemorySummary {
  readonly mode: MemoryMode;
  readonly templates: number;
  readonly active_templates: number;
  readonly exemplar_documents: number;
  readonly corrected_exemplars: number;
  readonly active_rules: number;
  readonly shadow_rules: number;
  readonly running_trials: number;
  readonly baseline_correction_rate?: number | null;
  readonly memory_correction_rate?: number | null;
}

export interface FieldMetric {
  readonly field_path: string;
  readonly corrections: number;
  readonly confirmations: number;
  readonly baseline_rate?: number | null;
  readonly memory_rate?: number | null;
}

export interface TemplateRow {
  readonly id: string;
  readonly document_type: string;
  readonly state: TemplateState;
  readonly member_count: number;
  readonly exemplar_documents: number;
  readonly anchor_tokens: readonly string[];
  readonly activated_at?: string | null;
  readonly baseline_correction_rate?: number | null;
  readonly memory_correction_rate?: number | null;
  readonly fields: readonly FieldMetric[];
}

export interface RuleRow {
  readonly id: string;
  readonly template_id: string;
  readonly field_path: string;
  readonly anchor_norm: string;
  readonly offset_dx: number;
  readonly offset_dy: number;
  readonly value_token_count: number;
  readonly support: number;
  readonly replay_hits: number;
  readonly replay_total: number;
  readonly wilson_lower?: number | null;
  readonly live_hits: number;
  readonly live_total: number;
  readonly state: RuleState;
  readonly activated_at?: string | null;
  readonly retired_reason?: string | null;
  readonly sentence: string;
}

export interface TrialRow {
  readonly id: string;
  readonly template_id: string;
  readonly document_type: string;
  readonly state: TrialState;
  readonly on_docs: number;
  readonly off_docs: number;
  readonly on_correction_rate?: number | null;
  readonly off_correction_rate?: number | null;
  readonly p_value?: number | null;
  readonly decision_reason?: string | null;
  readonly started_at: string;
  readonly decided_at?: string | null;
  readonly sentence: string;
}

export interface LearnedField {
  readonly field_path: string;
  readonly corrections: number;
  readonly confirmations: number;
  readonly sentence: string;
  readonly anchor_state?: string | null;
}

export interface WorkItemMemory {
  readonly applied: boolean;
  readonly arm?: string | null;
  readonly injected: boolean;
  readonly template_id?: string | null;
  readonly document_type?: string | null;
  readonly template_state?: string | null;
  readonly layout_documents: number;
  readonly exemplar_documents_used: number;
  readonly fields: readonly LearnedField[];
  readonly headline: string;
}
