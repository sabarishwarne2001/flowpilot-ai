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
