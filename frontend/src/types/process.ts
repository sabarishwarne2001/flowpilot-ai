/**
 * ARCH49-S2:process-types — Process Intelligence & the Governed Exception Agent.
 *
 * Mirrors backend/app/schemas/process_intel.py (verify_arch49 W3 compares the
 * field lists) and the agent's closed vocabulary in
 * backend/app/services/process_intel/agent/vocabulary.py (verify_arch49 W3
 * compares the keys). Money is USD micros from the cost truth: a NULL cost
 * basis is UNKNOWN and reported beside the figure, never shown as zero.
 */

export type ObjectType = "DOCUMENT" | "CASE" | "POSTING" | "REVIEW_ITEM" | "FINDING" | "EXECUTION";
export type SlaObjectType = "REVIEW_ITEM" | "CASE" | "POSTING";
export type ProposalStatus =
  | "PROPOSED"
  | "AUTO_SCHEDULED"
  | "APPLIED"
  | "AUTO_APPLIED"
  | "REJECTED"
  | "UNDONE"
  | "SUPERSEDED"
  | "FAILED";
export type RejectReason = "WRONG_DECISION" | "WRONG_EVIDENCE" | "NEEDS_CONTEXT" | "NOT_NOW" | "OTHER";
export type ProposalKind =
  | "extraction.approve_consensus"
  | "assertion.accept_engine"
  | "anomaly.confirm"
  | "anomaly.dismiss"
  | "merge.merge"
  | "merge.separate"
  | "split.approve"
  | "table.accept"
  | "corroboration.confirm"
  | "posting.retry"
  | "review.route"
  | "case.reevaluate"
  | "case.request_document";

export const OBJECT_TYPES: readonly ObjectType[] = ["DOCUMENT", "CASE", "POSTING", "REVIEW_ITEM", "FINDING", "EXECUTION"];
export const SLA_OBJECT_TYPES: readonly SlaObjectType[] = ["REVIEW_ITEM", "CASE", "POSTING"];

export const OBJECT_TYPE_LABELS: Readonly<Record<ObjectType, string>> = {
  DOCUMENT: "Documents",
  CASE: "Cases",
  POSTING: "ERP postings",
  REVIEW_ITEM: "Review items",
  FINDING: "Radar findings",
  EXECUTION: "Flow runs",
};

export const PROPOSAL_STATUSES: readonly ProposalStatus[] = [
  "PROPOSED", "AUTO_SCHEDULED", "APPLIED", "AUTO_APPLIED", "REJECTED", "UNDONE", "SUPERSEDED", "FAILED",
];
export const LIVE_STATUSES: readonly ProposalStatus[] = ["PROPOSED", "AUTO_SCHEDULED"];

export const STATUS_LABELS: Readonly<Record<ProposalStatus, string>> = {
  PROPOSED: "Waiting for you",
  AUTO_SCHEDULED: "Applies itself soon",
  APPLIED: "Approved",
  AUTO_APPLIED: "Applied itself",
  REJECTED: "Rejected",
  UNDONE: "Undone",
  SUPERSEDED: "Overtaken",
  FAILED: "Refused by the owner",
};

export const STATUS_TONES: Readonly<Record<ProposalStatus, string>> = {
  PROPOSED: "bg-primary/10 text-primary",
  AUTO_SCHEDULED: "bg-amber-500/15 text-amber-800 dark:text-amber-300",
  APPLIED: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  AUTO_APPLIED: "bg-emerald-500/15 text-emerald-800 dark:text-emerald-300",
  REJECTED: "bg-muted text-muted-foreground",
  UNDONE: "bg-muted text-muted-foreground",
  SUPERSEDED: "bg-muted text-muted-foreground",
  FAILED: "bg-destructive/10 text-destructive",
};

/** The agent's proposal kinds; the words are the server's KindSpec labels. */
export const PROPOSAL_KIND_LABELS: Readonly<Record<ProposalKind, string>> = {
  "extraction.approve_consensus": "Accept the extractors' majority reading",
  "assertion.accept_engine": "Accept the clause engine's verdict",
  "anomaly.confirm": "Confirm the radar finding",
  "anomaly.dismiss": "Dismiss the radar finding",
  "merge.merge": "Merge the two records",
  "merge.separate": "Keep the two records separate",
  "split.approve": "Approve the split plan",
  "table.accept": "Accept the table's figures",
  "corroboration.confirm": "Confirm the material differences",
  "posting.retry": "Send the posting again",
  "review.route": "Give the item to the person best placed to decide it",
  "case.reevaluate": "Re-evaluate the case against its documents as they are now",
  "case.request_document": "Request the missing document",
};

/** Only these can ever apply themselves: ARCH-35 automates their decision (a conformal bound). */
export const AUTO_CAPABLE_KINDS: readonly ProposalKind[] = ["extraction.approve_consensus", "assertion.accept_engine"];

/** Why a proposal waits for a person (the agent's own reasons, then ARCH-35's). */
export const HOLD_LABELS: Readonly<Record<string, string>> = {
  NOT_AUTO_CAPABLE: "No calibrated error bound exists for this kind of decision — a person always decides it",
  POLICY_OFF: "Automatic resolution is off for this workspace",
  KIND_OFF: "Automatic resolution is not switched on for this kind",
  OWNER_NOT_ADMIN: "Whoever switched automatic resolution on is no longer a workspace admin",
  NOT_CALIBRATION_HELD: "Held for another reason (a disagreement, an escalation, a memory trial or an accuracy audit)",
  ENGINE_NOT_PASS: "The calibrated bound covers only an engine PASS",
  INJECTION_SUSPECTED: "A source reads like instructions to an AI — a person must look",
  OPEN_DISCUSSION: "People are discussing this item",
  NO_CALIBRATED_AUTONOMY: "Calibrated autonomy is not on the plan",
  no_model: "No calibration model for this decision yet",
  cold_start: "The calibration model has too few labels yet",
  stale: "The calibration model is stale",
  suspended: "The calibration model is suspended",
  not_achievable: "The tenant's error limit is not achievable with this model",
  below_threshold: "Below the calibrated threshold",
  audit_sample: "Drawn for an accuracy audit",
};

export const REJECT_REASONS: readonly RejectReason[] = ["WRONG_DECISION", "WRONG_EVIDENCE", "NEEDS_CONTEXT", "NOT_NOW", "OTHER"];
export const REJECT_REASON_LABELS: Readonly<Record<RejectReason, string>> = {
  WRONG_DECISION: "The decision is wrong",
  WRONG_EVIDENCE: "It read the evidence wrong",
  NEEDS_CONTEXT: "It needs context the agent cannot see",
  NOT_NOW: "Not now",
  OTHER: "Another reason",
};

export const TOOL_LABELS: Readonly<Record<string, string>> = {
  "read.review_item": "Read the review item",
  "read.verification": "Read the extraction",
  "read.assertion": "Read the clause assertion",
  "read.finding": "Read the radar finding",
  "read.precedent": "Read reviewers' precedent",
  "read.merge_candidate": "Read the merge candidate",
  "read.split": "Read the split plan",
  "read.table": "Read the table checks",
  "read.corroboration": "Read the comparison",
  "read.obligation": "Read the obligation",
  "read.posting": "Read the posting and its attempts",
  "read.case": "Read the case",
  "read.calibration": "Read the calibration model",
  "read.lock": "Read the item's lock",
  "read.threads": "Read the open discussion",
  "agent.resolve_review_item": "Propose a resolution",
  "agent.assign_review_item": "Propose an assignee",
  "agent.reevaluate_case": "Propose re-evaluating the case",
  "agent.request_case_document": "Propose requesting a document",
};

export const PREDICTION_STATE_LABELS: Readonly<Record<string, string>> = {
  AT_RISK: "At risk",
  OK: "On track",
  BREACHED: "Breached",
};

// -- wire shapes (backend/app/schemas/process_intel.py) --------------------------------------------

export interface ObjectTypeSummary {
  readonly object_type: string;
  readonly label: string;
  readonly objects: number;
  readonly events: number;
  readonly shares_events_with: Readonly<Record<string, number>>;
}

export interface SourceCursor {
  readonly source: string;
  readonly watermark: string;
  readonly caught_up: boolean;
  readonly last_run_at: string;
  readonly events_written: number;
}

export interface ReliabilityBin {
  readonly bin: number;
  readonly low: number;
  readonly high: number;
  readonly count: number;
  readonly predicted: number;
  readonly observed: number;
}

export interface ModelRun {
  readonly id: string;
  readonly status: string;
  readonly reason: string | null;
  readonly target_hours: number;
  readonly instances_train: number;
  readonly instances_holdout: number;
  readonly snapshots_train: number;
  readonly snapshots_holdout: number;
  readonly base_rate: number | null;
  readonly brier: number | null;
  readonly brier_baseline: number | null;
  readonly skill: number | null;
  readonly reliability: readonly ReliabilityBin[];
  readonly importances: Readonly<Record<string, number>>;
  readonly trained_at: string;
}

export interface AgentPolicy {
  readonly planning_enabled: boolean;
  readonly auto_apply_enabled: boolean;
  readonly auto_apply_kinds: readonly string[];
  readonly hold_minutes: number;
  readonly enabled_by_user_id: string | null;
  readonly enabled_at: string | null;
  readonly is_default: boolean;
  readonly auto_capable_kinds: readonly string[];
}

export interface AgentPolicyUpdate {
  readonly planning_enabled: boolean;
  readonly auto_apply_enabled: boolean;
  readonly auto_apply_kinds: readonly string[];
  readonly hold_minutes: number;
}

export interface ProcessOverview {
  readonly events: number;
  readonly first_at: string | null;
  readonly last_at: string | null;
  readonly object_types: readonly ObjectTypeSummary[];
  readonly sources: readonly SourceCursor[];
  readonly runs: Readonly<Record<string, ModelRun | null>>;
  readonly at_risk: number;
  readonly breached: number;
  readonly proposals: Readonly<Record<string, number>>;
  readonly policy: AgentPolicy;
}

export interface Durations {
  readonly mean: number;
  readonly median: number;
  readonly p90: number;
}

export interface CostSummary {
  readonly known_micros: number;
  readonly reconciled_micros: number;
  readonly reconciled_share: number;
  readonly known_events: number;
  readonly unknown_events: number;
  readonly revenue_micros: number;
  readonly unknown_cost_share: number;
  readonly objects: number;
  readonly mean_known_micros: number;
  readonly mean_reconciled_micros: number;
  readonly truncated?: boolean;
}

export interface DfgActivity {
  readonly activity: string;
  readonly events: number;
  readonly objects: number;
}

export interface DfgEdge {
  readonly source: string;
  readonly target: string;
  readonly count: number;
  readonly seconds: Durations;
}

export interface DfgGraph {
  readonly objects: number;
  readonly activities: readonly DfgActivity[];
  readonly edges: readonly DfgEdge[];
  readonly starts: readonly { readonly activity: string; readonly count: number }[];
  readonly ends: readonly { readonly activity: string; readonly count: number }[];
}

export interface Variant {
  readonly id: string;
  readonly activities: readonly string[];
  readonly length: number;
  readonly count: number;
  readonly share: number;
  readonly seconds: Durations;
  readonly object_ids: readonly string[];
  readonly cost: CostSummary;
  readonly touches: number;
  readonly agent_events: number;
}

export interface DiscoveryOut {
  readonly object_type: string;
  readonly window_days: number;
  readonly truncated: boolean;
  readonly throughput_seconds: Durations;
  readonly graph: DfgGraph;
  readonly variants: readonly Variant[];
  readonly variant_count: number;
  readonly cost: CostSummary | null;
  readonly touches_per_object: number;
}

export interface TimelineEvent {
  readonly id: string;
  readonly activity: string;
  readonly occurred_at: string;
  readonly source: string;
  readonly actor_kind: string;
  readonly actor_user_id: string | null;
  readonly attributes: Readonly<Record<string, unknown>>;
  readonly objects: readonly { readonly object_type: string; readonly object_id: string; readonly qualifier: string }[];
}

export interface TimelineOut {
  readonly object_type: string;
  readonly object_id: string;
  readonly events: readonly TimelineEvent[];
}

export interface FlowConformance {
  readonly rule_id: string;
  readonly name: string;
  readonly is_active: boolean;
  readonly nodes: number;
  readonly executions: number;
  readonly replayed: number;
  readonly fitting: number;
  readonly graph_changed_since: number;
  readonly in_progress: number;
  readonly fitness: number | null;
  readonly missing_at: Readonly<Record<string, number>>;
  readonly remaining_at: Readonly<Record<string, number>>;
  readonly unknown_steps: Readonly<Record<string, number>>;
  readonly worst: readonly { readonly execution_id: string; readonly fitness: number }[];
  readonly error?: string;
}

export interface TemplateConformance {
  readonly template_id: string;
  readonly key: string;
  readonly version: number;
  readonly name: string;
  readonly slots: readonly { readonly doc_type: string; readonly min_count: number }[];
  readonly cases: number;
  readonly finished: number;
  readonly replayed: number;
  readonly fitting: number;
  readonly in_progress: number;
  readonly fitness: number | null;
  readonly missing_at: Readonly<Record<string, number>>;
  readonly remaining_at: Readonly<Record<string, number>>;
  readonly inconsistent_evaluations: number;
}

export interface ConformanceOut {
  readonly window_days: number;
  readonly flows: readonly FlowConformance[];
  readonly templates: readonly TemplateConformance[];
}

export interface SlaPolicy {
  readonly object_type: string;
  readonly target_hours: number;
  readonly at_risk_probability: number;
  readonly alerts_enabled: boolean;
  readonly is_default: boolean;
  readonly updated_at: string | null;
}

export interface SlaPolicyUpdate {
  readonly target_hours: number;
  readonly at_risk_probability: number;
  readonly alerts_enabled: boolean;
}

export interface Prediction {
  readonly object_type: string;
  readonly object_id: string;
  readonly kind: string | null;
  readonly started_at: string;
  readonly due_at: string;
  readonly probability: number;
  readonly state: string;
  readonly predicted_at: string;
  readonly alerted_at: string | null;
}

export interface SlaOut {
  readonly policies: readonly SlaPolicy[];
  readonly runs: Readonly<Record<string, ModelRun | null>>;
  readonly predictions: readonly Prediction[];
}

export interface CostOut {
  readonly window_days: number;
  readonly by_object_type: Readonly<Record<string, CostSummary>>;
}

export interface SweepOut {
  readonly report: Readonly<Record<string, unknown>>;
}

export interface ProposalRow {
  readonly id: string;
  readonly subject_type: "REVIEW_ITEM" | "CASE";
  readonly subject_kind: string;
  readonly subject_id: string;
  readonly work_item_id: string | null;
  readonly proposal_kind: ProposalKind;
  readonly label: string;
  readonly status: ProposalStatus;
  readonly confidence: number;
  readonly calibrated_probability: number | null;
  readonly subject_version: number;
  readonly verdict: string | null;
  readonly rationale: readonly string[];
  readonly holds: readonly string[];
  readonly auto: boolean;
  readonly apply_after: string | null;
  readonly waiting_until: string | null;
  readonly injection_suspected: boolean;
  readonly decided_by_user_id: string | null;
  readonly decided_at: string | null;
  readonly reject_reason: RejectReason | null;
  readonly applied_at: string | null;
  readonly resolution: string | null;
  readonly failure: string | null;
  readonly created_at: string;
}

export interface ProposalList {
  readonly items: readonly ProposalRow[];
  readonly total: number;
  readonly counts: Readonly<Record<string, number>>;
}

export interface ToolCallOut {
  readonly seq: number;
  readonly tool: string;
  readonly arguments: Readonly<Record<string, unknown>>;
  readonly outcome: "OK" | "EMPTY" | "REFUSED";
  readonly detail: Readonly<Record<string, unknown>>;
}

/** What a source says: delimited, untrusted data. Rendered as a quote — never as instructions, never as HTML. */
export interface ExcerptOut {
  readonly label: string;
  readonly fenced_text: string;
  readonly fence_nonce: string;
  readonly injection_flags: Readonly<Record<string, number>>;
}

export interface ProposalDetail {
  readonly proposal: ProposalRow;
  readonly evidence: Readonly<Record<string, unknown>>;
  readonly autonomy: Readonly<Record<string, unknown>>;
  readonly tool_calls: readonly ToolCallOut[];
  readonly excerpts: readonly ExcerptOut[];
}

export interface RejectIn {
  readonly reason: RejectReason;
}

export interface ApproveOut {
  readonly proposal: ProposalRow;
  readonly resolution: string | null;
  readonly upload_path: string | null;
}

/** The 409 codes an approve / reject / undo can meet. */
export type ProposalConflictCode =
  | "LOCKED"
  | "STALE_VERSION"
  | "ALREADY_RESOLVED"
  | "ALREADY_DECIDED"
  | "REFUSED"
  | "NOT_SCHEDULED";

export const CONFLICT_MESSAGES: Readonly<Record<ProposalConflictCode, string>> = {
  LOCKED: "Someone is deciding this item right now. The proposal waits until their lock ends — it never breaks it.",
  STALE_VERSION: "The item changed after the agent read it, so the proposal was set aside.",
  ALREADY_RESOLVED: "Someone already decided this item, so the proposal was set aside.",
  ALREADY_DECIDED: "Someone already approved, rejected or undid this proposal.",
  REFUSED: "The owning service refused the decision; the reason is on the proposal.",
  NOT_SCHEDULED: "Only a scheduled automatic resolution can be undone, before it takes effect.",
};
