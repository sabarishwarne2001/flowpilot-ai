/** Phase 2 — TruthMesh, the cross-document digital twin (capability.truthmesh). */

export type Severity = "CRITICAL" | "HIGH" | "MEDIUM" | "LOW";
export type ConflictStatus = "OPEN" | "ACKNOWLEDGED" | "RESOLVED" | "DISMISSED";
export type LinkStatus = "AUTO" | "CONFIRMED" | "REJECTED";
export type RiskBand = "CRITICAL" | "HIGH" | "ELEVATED" | "LOW";
export type Scenario = "DELAY" | "CLAUSE_INVOKED" | "AMOUNT_CHANGE" | "TERMINATION" | "PARTY_DEFAULT";
export type Dimension = "FINANCIAL" | "OPERATIONAL" | "LEGAL";

export const SEVERITIES: readonly Severity[] = ["CRITICAL", "HIGH", "MEDIUM", "LOW"];

export interface MeshNode {
  readonly work_item_id: string;
  readonly kind: string;
  readonly kind_label: string;
  readonly rank: number;
  readonly title: string;
  readonly filename: string;
  readonly document_number: string | null;
  readonly counterparty: string | null;
  readonly currency: string | null;
  readonly amount_micros: number | null;
  readonly net_amount_micros: number | null;
  readonly document_date: string | null;
  readonly effective_date: string | null;
  readonly end_date: string | null;
  readonly risk_score: number;
  readonly risk_band: RiskBand;
  readonly degree: number;
  readonly open_conflicts?: number | null;
}

export interface MeshSignal {
  readonly kind: string;
  readonly value: string | number;
  readonly weight: number;
  readonly child?: string;
  readonly parties?: number;
}

export interface MeshLink {
  readonly id: string;
  readonly source: string;
  readonly target: string;
  readonly relation: string;
  readonly relation_label: string;
  readonly directed: boolean;
  readonly strength: number;
  readonly method: string;
  readonly signals: readonly MeshSignal[];
  readonly status: LinkStatus;
  readonly other?: MeshNode | null;
  readonly outgoing?: boolean | null;
}

export interface DocumentValue {
  readonly work_item_id: string;
  readonly title: string;
  readonly kind: string;
  readonly display: string;
  readonly role?: string | null;
  readonly quote?: string | null;
  readonly micros?: number | null;
}

export interface MeshConflict {
  readonly id: string;
  readonly kind: string;
  readonly kind_label: string;
  readonly concept: string;
  readonly severity: Severity;
  readonly status: ConflictStatus;
  readonly title: string;
  readonly summary: string;
  readonly relation: string | null;
  readonly work_item_ids: readonly string[];
  readonly document_values: readonly DocumentValue[];
  readonly exposure_micros: number | null;
  readonly currency: string | null;
  readonly auto_resolved: boolean;
  readonly resolution_note: string | null;
  readonly resolved_at: string | null;
  readonly first_seen_at: string;
  readonly last_seen_at: string;
}

export interface CountRow {
  readonly kind?: string | null;
  readonly relation?: string | null;
  readonly label: string;
  readonly count: number;
}

export interface MeshOverview {
  readonly state: {
    readonly status: "EMPTY" | "BUILDING" | "READY" | "FAILED";
    readonly last_built_at: string | null;
    readonly build_ms: number | null;
    readonly engine_version: string | null;
    readonly error: string | null;
  };
  readonly documents: number;
  readonly links: number;
  readonly clusters: number;
  readonly unlinked_documents: number;
  readonly open_conflicts: number;
  readonly by_severity: Readonly<Record<Severity, number>>;
  readonly by_kind: readonly CountRow[];
  readonly exposure_micros: number;
  readonly exposure_currency: string | null;
  readonly mixed_currencies: boolean;
  readonly risk_index: number;
  readonly risk_band: RiskBand;
  readonly relations: readonly CountRow[];
  readonly kinds: readonly CountRow[];
  readonly top_risks: readonly MeshNode[];
  readonly top_conflicts: readonly MeshConflict[];
}

export interface MeshGraph {
  readonly nodes: readonly MeshNode[];
  readonly links: readonly MeshLink[];
  readonly truncated: boolean;
}

export interface MeshTerm {
  readonly value: string | number;
  readonly unit: string;
  readonly quote: string;
  readonly source: "field" | "text";
}

export interface MeshFact {
  readonly concept: string;
  readonly label: string;
  readonly class: string;
  readonly display: string;
}

export interface MeshDocument {
  readonly node: MeshNode;
  readonly facts: readonly MeshFact[];
  readonly terms: Readonly<Record<string, MeshTerm>>;
  readonly identifiers: readonly string[];
  readonly references: readonly string[];
  readonly text_references: readonly string[];
  readonly parties: readonly string[];
  readonly links: readonly MeshLink[];
  readonly conflicts: readonly MeshConflict[];
}

export interface MatrixRow {
  readonly conflict: MeshConflict;
  readonly cells: Readonly<Record<string, { display: string | null; role: string | null; quote: string | null }>>;
}

export interface MeshMatrix {
  readonly columns: readonly MeshNode[];
  readonly rows: readonly MatrixRow[];
}

export interface RippleEffect {
  readonly dimension: Dimension;
  readonly severity: Severity;
  readonly title: string;
  readonly detail: string;
  readonly amount_micros?: number;
  readonly currency?: string;
  readonly quote?: string;
  readonly breach?: boolean;
  readonly exposure?: boolean;
  readonly date_from?: string;
  readonly date_to?: string;
}

export interface RippleNode {
  readonly work_item_id: string;
  readonly title: string;
  readonly filename: string;
  readonly kind: string;
  readonly kind_label: string;
  readonly depth: number;
  readonly impact: number;
  readonly via: string | null;
  readonly path: readonly string[];
  readonly effects: readonly RippleEffect[];
}

export interface RippleResult {
  readonly origin: string;
  readonly scenario: Scenario;
  readonly scenario_label: string;
  readonly parameters: Readonly<Record<string, unknown>>;
  readonly nodes: readonly RippleNode[];
  readonly edges: readonly { source: string; target: string; relation: string; strength: number }[];
  readonly summary: {
    readonly documents_affected: number;
    readonly max_depth: number;
    readonly financial_exposure_micros: number;
    readonly currency: string | null;
    readonly breaches: number;
    readonly effects: Readonly<Record<Dimension, number>>;
    readonly legal_quotes: number;
  };
  readonly clause?: { quote: string; label: string; clause_type: string; found: boolean };
}

export interface SimulationSummary {
  readonly id: string;
  readonly origin_work_item_id: string;
  readonly scenario: Scenario;
  readonly title: string;
  readonly parameters: Readonly<Record<string, unknown>>;
  readonly documents_affected: number;
  readonly exposure_micros: number | null;
  readonly currency: string | null;
  readonly created_by_user_id: string | null;
  readonly created_at: string;
}

export interface Simulation extends SimulationSummary {
  readonly result: RippleResult;
}

export interface SimulationRequest {
  readonly work_item_id: string;
  readonly scenario: Scenario;
  readonly days?: number;
  readonly percent?: number;
  readonly clause?: string;
  readonly party?: string;
}

export const SCENARIO_LABELS: Readonly<Record<Scenario, { label: string; hint: string }>> = {
  DELAY: { label: "Delay", hint: "Delivery or performance slips by a number of days" },
  CLAUSE_INVOKED: { label: "Clause invoked", hint: "A clause of this document is invoked (a number such as 8.2, or its subject)" },
  AMOUNT_CHANGE: { label: "Amount change", hint: "The price or amount moves by a percentage" },
  TERMINATION: { label: "Termination", hint: "This agreement is terminated" },
  PARTY_DEFAULT: { label: "Counterparty default", hint: "The counterparty defaults or becomes insolvent" },
};
