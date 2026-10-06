/**
 * PHASE 4 — evidence for reviewers: where each value is printed, and the
 * page images to draw it on. Boxes are normalised to the page (0..1, top-left).
 */
import apiClient from "@/services/api/client";
import { EVIDENCE_ENDPOINTS } from "@/services/api/endpoints";

export interface EvidenceLocation {
  field: string | null;
  value: string;
  source: "extracted" | "candidate";
  page: number;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export interface DocumentEvidence {
  work_item_id: string;
  renderable: boolean;
  pages: { page_number: number; width: number | null; height: number | null }[];
  locations: EvidenceLocation[];
  not_found: { field: string | null; value: string; source: string }[];
}

/** `candidates` are `field:value` pairs, e.g. what each verification agent read. */
export const getDocumentEvidence = async (
  workspaceId: string,
  workItemId: string,
  candidates: readonly string[],
): Promise<DocumentEvidence> => {
  const params = new URLSearchParams();
  candidates.forEach((candidate) => params.append("candidate", candidate));
  const response = await apiClient.get<DocumentEvidence>(
    EVIDENCE_ENDPOINTS.evidence(workspaceId, workItemId),
    { params },
  );
  return response.data;
};

/** The page as a PNG blob (fetched with the user's credentials, unlike an <img src>). */
export const getDocumentPageImage = async (
  workspaceId: string,
  workItemId: string,
  page: number,
): Promise<Blob> => {
  const response = await apiClient.get<Blob>(
    EVIDENCE_ENDPOINTS.pageImage(workspaceId, workItemId, page),
    { params: { dpi: 110 }, responseType: "blob" },
  );
  return response.data;
};

/* N-020 items 6/7 — the document viewer. */

export interface DocumentText {
  work_item_id: string;
  text: string;
  characters: number;
  truncated: boolean;
}

export interface FieldCorrection {
  id: string;
  field_path: string;
  previous_value: unknown;
  corrected_value: unknown;
  corrected_by_user_id: string | null;
  reason: string | null;
  created_at: string;
}

export interface FieldCorrectionResult {
  work_item_id: string;
  extracted_entities: Record<string, unknown>;
  corrected: FieldCorrection[];
  extraction_memory_fields: string[];
}

export const getDocumentText = async (workspaceId: string, workItemId: string): Promise<DocumentText> => {
  const response = await apiClient.get<DocumentText>(EVIDENCE_ENDPOINTS.text(workspaceId, workItemId));
  return response.data;
};

export const getFieldHistory = async (workspaceId: string, workItemId: string): Promise<FieldCorrection[]> => {
  const response = await apiClient.get<FieldCorrection[]>(EVIDENCE_ENDPOINTS.fieldHistory(workspaceId, workItemId));
  return response.data;
};

export const correctFields = async (
  workspaceId: string,
  workItemId: string,
  corrections: Record<string, string | number | boolean | null>,
  reason?: string,
): Promise<FieldCorrectionResult> => {
  const response = await apiClient.patch<FieldCorrectionResult>(EVIDENCE_ENDPOINTS.fields(workspaceId, workItemId), {
    corrections,
    ...(reason ? { reason } : {}),
  });
  return response.data;
};

export interface FieldEditability {
  work_item_id: string;
  editable: boolean;
  code: string | null;
  message: string | null;
}

/** Whether the caller may correct this document's fields now, and if not, why (review queue, legal hold, role). */
export const getFieldEditability = async (workspaceId: string, workItemId: string): Promise<FieldEditability> => {
  const response = await apiClient.get<FieldEditability>(EVIDENCE_ENDPOINTS.fields(workspaceId, workItemId));
  return response.data;
};
