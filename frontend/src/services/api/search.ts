/**
 * PHASE 4 — global search (Ctrl+K).
 *
 * Documents (by file name or any extracted value, e.g. an invoice number),
 * entity records and cases, from every workspace of the organization the
 * caller may open - never another. Each hit names its workspace so the
 * palette can open it there.
 */
import apiClient from "@/services/api/client";
import { SEARCH_ENDPOINTS } from "@/services/api/endpoints";

export type SearchHitKind = "DOCUMENT" | "ENTITY" | "CASE";

export interface SearchHit {
  kind: SearchHitKind;
  id: string;
  title: string;
  subtitle: string | null;
  workspace_id: string;
  workspace_slug: string;
  workspace_name: string;
}

export interface SearchResponse {
  query: string;
  workspaces_searched: number;
  documents: SearchHit[];
  entities: SearchHit[];
  cases: SearchHit[];
}

/** The server refuses shorter queries (422). */
export const MIN_SEARCH_LENGTH = 2;

export const searchOrganization = async (
  organizationId: string,
  query: string,
  signal?: AbortSignal,
): Promise<SearchResponse> => {
  const response = await apiClient.get<SearchResponse>(
    SEARCH_ENDPOINTS.organization(organizationId),
    signal
      ? { params: { q: query, limit: 5 }, signal }
      : { params: { q: query, limit: 5 } },
  );
  return response.data;
};
