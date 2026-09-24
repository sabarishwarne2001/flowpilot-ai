/**
 * ARCH43-S2:types — the packet dicer's API shapes. Mirrors
 * backend/app/schemas/packets.py field for field; verify_arch43 T9 compares
 * the two lists so they cannot drift.
 */

export type PacketSplitStatus =
  | "PROPOSED"
  | "SINGLE"
  | "APPROVED"
  | "APPLYING"
  | "APPLIED"
  | "REJECTED"
  | "FAILED"
  | "SUPERSEDED";

export interface SegmentRow {
  readonly id: string;
  readonly ordinal: number;
  readonly page_start: number;
  readonly page_end: number;
  readonly document_type: string | null;
  readonly confidence: number | null;
  readonly title: string | null;
  readonly child_work_item_id: string | null;
}

export interface PageScore {
  readonly page: number;
  readonly p: number;
  readonly doc_type: string;
  readonly features: Readonly<Record<string, number>> | null;
}

export interface PacketSplitRow {
  readonly id: string;
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly status: PacketSplitStatus;
  readonly source: "MODEL" | "REVIEWER";
  readonly page_count: number;
  readonly segment_count: number;
  readonly certainty: number | null;
  readonly model_version: string;
  readonly proposed_at: string;
  readonly decided_at: string | null;
  readonly applied_at: string | null;
}

export interface PacketSplitList {
  readonly items: readonly PacketSplitRow[];
  readonly total: number;
}

export interface PacketSplitDetail {
  readonly split: PacketSplitRow;
  readonly threshold: number;
  readonly segments: readonly SegmentRow[];
  readonly pages: readonly PageScore[];
  readonly failure_reason: string | null;
}

export interface LineageParent {
  readonly work_item_id: string;
  readonly original_filename: string;
  readonly page_start: number | null;
  readonly page_end: number | null;
}

export interface LineageChild {
  readonly ordinal: number;
  readonly page_start: number;
  readonly page_end: number;
  readonly document_type: string | null;
  readonly work_item_id: string | null;
  readonly original_filename: string | null;
  readonly pipeline_stage: string | null;
}

export interface LineageSplit {
  readonly split_id: string;
  readonly status: PacketSplitStatus;
}

export interface WorkItemLineage {
  readonly work_item_id: string;
  readonly parent: LineageParent | null;
  readonly split: LineageSplit | null;
  readonly children: readonly LineageChild[];
}
