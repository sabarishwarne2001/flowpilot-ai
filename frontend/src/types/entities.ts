/** ARCH42-S2:types — mirrors backend/app/schemas/entities.py field for field (verify_arch42 E10). */

export type EntityKind = "PERSON" | "ORGANIZATION" | "ADDRESS" | "ACCOUNT" | "ASSET" | "SHIPMENT";
export const ENTITY_KINDS: readonly EntityKind[] = ["PERSON", "ORGANIZATION", "ADDRESS", "ACCOUNT", "ASSET", "SHIPMENT"];
export const ENTITY_KIND_LABELS: Readonly<Record<EntityKind, string>> = {
  PERSON: "People",
  ORGANIZATION: "Organizations",
  ADDRESS: "Addresses",
  ACCOUNT: "Accounts",
  ASSET: "Assets",
  SHIPMENT: "Shipments",
};

export interface EntityPotential {
  readonly documents: number;
}

export interface ModelRow {
  readonly kind: string;
  readonly version?: number | null;
  readonly source: string;
  readonly pair_count: number;
  readonly converged: boolean;
  readonly fitted_at?: string | null;
}

export interface EntitySummary {
  readonly counts_by_kind: Readonly<Record<string, number>>;
  readonly records: number;
  readonly documents_linked: number;
  readonly open_reviews: number;
  readonly open_conflicts: number;
  readonly models: readonly ModelRow[];
}

export interface EntityRow {
  readonly id: string;
  readonly kind: EntityKind;
  readonly display_name: string;
  readonly documents: number;
  readonly mentions: number;
  readonly identifier_kinds: readonly string[];
  readonly merged_records: number;
  readonly last_seen_at?: string | null;
}

export interface EntityList {
  readonly items: readonly EntityRow[];
  readonly total: number;
  readonly page: number;
  readonly page_size: number;
  readonly matched_by_identifier: boolean;
}

export interface IdentifierRow {
  readonly id: string;
  readonly kind: string;
  readonly display: string;
  readonly derived: boolean;
  readonly entity_id: string;
}

export interface DocumentRow {
  readonly mention_id: string;
  readonly work_item_id: string;
  readonly filename: string;
  readonly role: string;
  readonly field_path: string;
  readonly decision: string;
  readonly method: string;
  readonly probability?: number | null;
  readonly entity_id: string;
  readonly created_at: string;
}

export interface RelationshipRow {
  readonly relation: string;
  readonly direction: string;
  readonly other_id: string;
  readonly other_kind: string;
  readonly other_name: string;
  readonly documents: number;
}

export interface MemberRow {
  readonly id: string;
  readonly display_name: string;
  readonly merged_at?: string | null;
  readonly merge_reason?: string | null;
  readonly mentions: number;
}

export interface CandidateRow {
  readonly id: string;
  readonly left_id: string;
  readonly left_name: string;
  readonly right_id: string;
  readonly right_name: string;
  readonly kind: string;
  readonly reason: string;
  readonly probability: number;
  readonly weight: number;
  readonly conflict_kinds: readonly string[];
  readonly status: string;
  readonly comparison: Readonly<Record<string, number | null>>;
  readonly created_at: string;
}

export interface ObligationsPlaceholder {
  readonly available: boolean;
  readonly milestone: string;
  readonly items: readonly unknown[];
}

export interface Entity360 {
  readonly entity: EntityRow;
  readonly first_seen_at?: string | null;
  readonly split_from_id?: string | null;
  readonly identifiers: readonly IdentifierRow[];
  readonly documents: readonly DocumentRow[];
  readonly relationships: readonly RelationshipRow[];
  readonly members: readonly MemberRow[];
  readonly open_candidates: readonly CandidateRow[];
  readonly obligations: ObligationsPlaceholder;
}

export interface GraphNode {
  readonly id: string;
  readonly kind: EntityKind;
  readonly label: string;
  readonly documents: number;
  readonly depth: number;
}

export interface GraphEdge {
  readonly source: string;
  readonly target: string;
  readonly relation: string;
  readonly weight: number;
}

export interface EntityGraph {
  readonly root_id: string;
  readonly nodes: readonly GraphNode[];
  readonly edges: readonly GraphEdge[];
  readonly truncated: boolean;
}

export interface EntityChip {
  readonly entity_id: string;
  readonly kind: EntityKind;
  readonly display_name: string;
  readonly role: string;
  readonly decision: string;
  readonly field_path: string;
}

export interface WorkItemEntities {
  readonly chips: readonly EntityChip[];
  readonly resolved: boolean;
}

export interface ResolveResult {
  readonly resolved: boolean;
  readonly detail: Readonly<Record<string, number | boolean | string>>;
}

export const RELATION_LABELS: Readonly<Record<string, string>> = {
  EMPLOYED_BY: "employed by",
  CONTRACTED_WITH: "contracted with",
  SUPPLIES: "supplies",
  LEASES_FROM: "leases from",
  OCCUPIES: "occupies",
  LESSOR_OF: "lessor of",
  SHIPPED: "shipped",
  CONSIGNED_TO: "consigned to",
  CONTAINS: "contains",
  HOLDS_ACCOUNT: "holds account",
  LOCATED_AT: "located at",
};
