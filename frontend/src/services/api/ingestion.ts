/**
 * ARCH-38 — batch ingestion API client.
 *
 * `uploadFileResumable` is the only place in the frontend that knows how a
 * resumable upload works. It slices the file at the part size the SERVER
 * chose, and before sending anything it asks the server which parts it already
 * holds. That single call is what makes a page reload survivable: the browser
 * keeps no record of its own, so there is nothing to go stale.
 */

import type { AxiosProgressEvent } from "axios";

import apiClient from "@/services/api/client";
import {
  INGESTION_ENDPOINTS,
  WORK_ITEM_ENDPOINTS,
} from "@/services/api/endpoints";

const JSON_HEADERS = { Accept: "application/json" } as const;

export interface BatchFileInput {
  readonly client_key: string;
  readonly filename: string;
  readonly size_bytes: number;
  readonly sha256?: string;
}

export interface BatchItem {
  readonly id: string;
  readonly client_key: string;
  readonly filename: string;
  readonly size_bytes: number;
  readonly status: string;
  readonly error_code?: string | null;
  readonly error_detail?: string | null;
  readonly work_item_id?: string | null;
  readonly upload_session_id?: string | null;
}

export interface Batch {
  readonly id: string;
  readonly status: string;
  readonly source: string;
  readonly total_items: number;
  readonly completed_items: number;
  readonly failed_items: number;
  readonly created_at: string;
  readonly completed_at?: string | null;
  readonly items: readonly BatchItem[];
}

export interface UploadSession {
  readonly id: string;
  readonly filename: string;
  readonly part_size: number;
  readonly parts_received: readonly number[];
  readonly status: string;
  readonly expires_at: string;
  readonly total_size?: number | null;
}

export interface BulkItemResult {
  readonly work_item_id: string;
  readonly outcome: "ok" | "refused" | "skipped";
  readonly code?: string | null;
  readonly detail?: string | null;
}

export interface BulkActionResult {
  readonly action: string;
  readonly succeeded: number;
  readonly refused: number;
  readonly results: readonly BulkItemResult[];
  readonly export_body?: string | null;
  readonly export_mime_type?: string | null;
}

export interface DocumentPreset {
  readonly id: string;
  readonly organization_id?: string | null;
  readonly industry: string;
  readonly document_type: string;
  readonly version: number;
  readonly label: string;
  readonly description: string;
  readonly schema_fields: Record<string, unknown>;
  readonly assertions: readonly { sentence: string; severity: string }[];
  readonly classifier_hints: readonly string[];
  readonly redaction_profile?: string | null;
  readonly is_platform: boolean;
  readonly applied: boolean;
  readonly enabled: boolean;
  readonly safe_harbor_notice?: string | null;
}

export interface RetentionHold {
  readonly id: string;
  readonly workspace_id?: string | null;
  readonly work_item_id?: string | null;
  readonly reason: string;
  readonly reference?: string | null;
  readonly placed_at: string;
  readonly released_at?: string | null;
}

export const createBatch = async (
  workspaceId: string,
  files: readonly BatchFileInput[],
  source: "FILES" | "FOLDER" | "ARCHIVE" = "FILES",
): Promise<Batch> => {
  const response = await apiClient.post<Batch>(
    INGESTION_ENDPOINTS.batches(workspaceId),
    { source, files },
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getBatch = async (
  workspaceId: string,
  batchId: string,
): Promise<Batch> => {
  const response = await apiClient.get<Batch>(
    INGESTION_ENDPOINTS.batch(workspaceId, batchId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listBatches = async (workspaceId: string): Promise<Batch[]> => {
  const response = await apiClient.get<Batch[]>(
    INGESTION_ENDPOINTS.batches(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const createUploadSession = async (
  workspaceId: string,
  input: {
    filename: string;
    mime_type?: string;
    total_size?: number;
    sha256?: string;
    batch_item_id?: string;
  },
): Promise<UploadSession> => {
  const response = await apiClient.post<UploadSession>(
    INGESTION_ENDPOINTS.sessions(workspaceId),
    input,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getUploadSession = async (
  workspaceId: string,
  sessionId: string,
): Promise<UploadSession> => {
  const response = await apiClient.get<UploadSession>(
    INGESTION_ENDPOINTS.session(workspaceId, sessionId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const abortUploadSession = async (
  workspaceId: string,
  sessionId: string,
): Promise<void> => {
  await apiClient.delete(INGESTION_ENDPOINTS.session(workspaceId, sessionId));
};

export const putUploadPart = async (
  workspaceId: string,
  sessionId: string,
  partNumber: number,
  blob: Blob,
  signal?: AbortSignal,
  onUploadProgress?: (event: AxiosProgressEvent) => void,
): Promise<UploadSession> => {
  const response = await apiClient.put<UploadSession>(
    INGESTION_ENDPOINTS.sessionPart(workspaceId, sessionId, partNumber),
    blob,
    {
      headers: { "Content-Type": "application/octet-stream", ...JSON_HEADERS },
      ...(signal ? { signal } : {}),
      ...(onUploadProgress ? { onUploadProgress } : {}),
    },
  );
  return response.data;
};

export const completeUploadSession = async (
  workspaceId: string,
  sessionId: string,
): Promise<{ session_id: string; work_item_id: string; batch_id?: string | null }> => {
  const response = await apiClient.post<{
    session_id: string;
    work_item_id: string;
    batch_id?: string | null;
  }>(INGESTION_ENDPOINTS.sessionComplete(workspaceId, sessionId), undefined, {
    headers: JSON_HEADERS,
  });
  return response.data;
};

/** sha256 of the whole file, so `complete` can refuse a corrupted assembly. */
export const hashFile = async (file: File): Promise<string | undefined> => {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) {
    // Available only in secure contexts. Without it the server simply has no
    // checksum to compare against, which is a weaker upload, not a broken one.
    return undefined;
  }
  const buffer = await file.arrayBuffer();
  const digest = await subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
};

export interface ResumableProgress {
  readonly uploadedBytes: number;
  readonly totalBytes: number;
  readonly partsDone: number;
  readonly partsTotal: number;
}

/**
 * Upload one file through a resumable session.
 *
 * `sessionId` is passed when resuming an upload that a reload interrupted; the
 * server's `parts_received` decides what still has to be sent, so a resumed
 * upload re-sends nothing it does not have to.
 */
export const uploadFileResumable = async (
  workspaceId: string,
  file: File,
  options: {
    sessionId?: string;
    batchItemId?: string;
    signal?: AbortSignal;
    onProgress?: (progress: ResumableProgress) => void;
    onSession?: (sessionId: string) => void;
  } = {},
): Promise<{ sessionId: string; workItemId: string }> => {
  let session: UploadSession;
  if (options.sessionId) {
    session = await getUploadSession(workspaceId, options.sessionId);
  } else {
    const sha256 = await hashFile(file);
    session = await createUploadSession(workspaceId, {
      filename: file.name,
      ...(file.type ? { mime_type: file.type } : {}),
      total_size: file.size,
      ...(sha256 ? { sha256 } : {}),
      ...(options.batchItemId ? { batch_item_id: options.batchItemId } : {}),
    });
  }
  options.onSession?.(session.id);

  const partSize = session.part_size;
  const partsTotal = Math.max(1, Math.ceil(file.size / partSize));
  const held = new Set<number>(session.parts_received);

  for (let partNumber = 1; partNumber <= partsTotal; partNumber += 1) {
    if (options.signal?.aborted) {
      throw new DOMException("Upload cancelled", "AbortError");
    }
    if (held.has(partNumber)) {
      continue;
    }
    const start = (partNumber - 1) * partSize;
    const chunk = file.slice(start, Math.min(start + partSize, file.size));
    await putUploadPart(
      workspaceId,
      session.id,
      partNumber,
      chunk,
      options.signal,
    );
    held.add(partNumber);
    options.onProgress?.({
      uploadedBytes: Math.min(held.size * partSize, file.size),
      totalBytes: file.size,
      partsDone: held.size,
      partsTotal,
    });
  }

  const done = await completeUploadSession(workspaceId, session.id);
  return { sessionId: session.id, workItemId: done.work_item_id };
};

export const runBulkAction = async (
  workspaceId: string,
  input: {
    action: "delete" | "reprocess" | "export" | "tag";
    ids: readonly string[];
    idempotency_key: string;
    tags?: readonly string[];
    export_format?: "csv" | "json";
  },
): Promise<BulkActionResult> => {
  const response = await apiClient.post<BulkActionResult>(
    WORK_ITEM_ENDPOINTS.bulk(workspaceId),
    input,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listWorkspaceTags = async (
  workspaceId: string,
): Promise<string[]> => {
  const response = await apiClient.get<{ tags: string[] }>(
    WORK_ITEM_ENDPOINTS.tags(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data.tags;
};

export const listPresets = async (
  workspaceId: string,
): Promise<DocumentPreset[]> => {
  const response = await apiClient.get<DocumentPreset[]>(
    INGESTION_ENDPOINTS.presets(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const applyPreset = async (
  workspaceId: string,
  presetId: string,
): Promise<DocumentPreset> => {
  const response = await apiClient.post<DocumentPreset>(
    INGESTION_ENDPOINTS.presetApply(workspaceId),
    { preset_id: presetId },
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const setPresetEnabled = async (
  workspaceId: string,
  presetId: string,
  enabled: boolean,
): Promise<DocumentPreset> => {
  const response = await apiClient.post<DocumentPreset>(
    INGESTION_ENDPOINTS.presetEnabled(workspaceId, presetId),
    { enabled },
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listRetentionHolds = async (
  workspaceId: string,
): Promise<RetentionHold[]> => {
  const response = await apiClient.get<RetentionHold[]>(
    WORK_ITEM_ENDPOINTS.retentionHolds(workspaceId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};
