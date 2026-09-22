/** ARCH-32 — Redaction Studio API client. */

import { apiClient } from "@/services/api/client";
import type {
  RedactionBundle,
  RedactionJob,
  RedactionRegion,
} from "@/types/redaction";

const base = (workspaceId: string): string =>
  `/workspaces/${encodeURIComponent(workspaceId)}/redactions`;

export const startRedaction = async (
  workspaceId: string,
  workItemId: string,
  profileKey: string,
  renderDpi = 300,
): Promise<RedactionJob> => {
  const { data } = await apiClient.post<RedactionJob>(
    `/workspaces/${encodeURIComponent(workspaceId)}/work-items/${encodeURIComponent(
      workItemId,
    )}/redactions`,
    { profile_key: profileKey, render_dpi: renderDpi, restore_text_layer: true },
  );
  return data;
};

export const getRedaction = async (
  workspaceId: string,
  jobId: string,
): Promise<RedactionJob> => {
  const { data } = await apiClient.get<RedactionJob>(
    `${base(workspaceId)}/${encodeURIComponent(jobId)}`,
  );
  return data;
};

/**
 * The page preview URL.
 *
 * `burn=true` routes through the SAME rasterize/burn path the apply job uses,
 * which is what makes "Preview result" honest: there is no second renderer
 * that could disagree with the file that gets produced.
 *
 * The response carries `no-store`. This is a rendering of the UNREDACTED
 * document, and it must not outlive the request.
 */
export const pagePreviewUrl = (
  workspaceId: string,
  jobId: string,
  pageNumber: number,
  options: { readonly dpi?: number; readonly burn?: boolean } = {},
): string => {
  const params = new URLSearchParams();
  params.set("dpi", String(options.dpi ?? 110));
  if (options.burn) {params.set("burn", "true");}
  return `${base(workspaceId)}/${encodeURIComponent(jobId)}/pages/${pageNumber}.png?${params.toString()}`;
};

export const toggleRegion = async (
  workspaceId: string,
  jobId: string,
  regionId: string,
  enabled: boolean,
): Promise<RedactionRegion> => {
  const { data } = await apiClient.patch<RedactionRegion>(
    `${base(workspaceId)}/${encodeURIComponent(jobId)}/regions/${encodeURIComponent(regionId)}`,
    { enabled },
  );
  return data;
};

export const addRegion = async (
  workspaceId: string,
  jobId: string,
  region: {
    readonly page_number: number;
    readonly x0: number;
    readonly y0: number;
    readonly x1: number;
    readonly y1: number;
  },
): Promise<RedactionRegion> => {
  const { data } = await apiClient.post<RedactionRegion>(
    `${base(workspaceId)}/${encodeURIComponent(jobId)}/regions`,
    region,
  );
  return data;
};

export const applyRedaction = async (
  workspaceId: string,
  jobId: string,
): Promise<RedactionJob> => {
  const { data } = await apiClient.post<RedactionJob>(
    `${base(workspaceId)}/${encodeURIComponent(jobId)}/apply`,
    {},
  );
  return data;
};

export const getBundle = async (
  workspaceId: string,
  jobId: string,
): Promise<RedactionBundle> => {
  const { data } = await apiClient.get<RedactionBundle>(
    `${base(workspaceId)}/${encodeURIComponent(jobId)}/bundle`,
  );
  return data;
};

export const redactionKeys = {
  job: (workspaceId: string, jobId: string) =>
    ["redaction", workspaceId, jobId] as const,
  bundle: (workspaceId: string, jobId: string) =>
    ["redaction", "bundle", workspaceId, jobId] as const,
};
