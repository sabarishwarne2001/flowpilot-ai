/** ARCH44-S2:api-client — table intelligence. Every route is gated on capability.table_intelligence. */
import { apiClient } from "@/services/api/client";
import { downloadBlob } from "@/services/api/audit";
import type {
  CellEdit, ColumnRole, DocumentTables, ExportFormat, ExtractResult, TableDetail, TableList, TableStatus, TableVerdict,
} from "@/types/tables";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const one = (workspaceId: string, tableId: string): string => `${ws(workspaceId)}/tables/${encodeURIComponent(tableId)}`;
const doc = (workspaceId: string, workItemId: string): string =>
  `${ws(workspaceId)}/work-items/${encodeURIComponent(workItemId)}/tables`;

export const tableKeys = {
  all: (workspaceId: string) => ["tables", workspaceId] as const,
  list: (workspaceId: string, status?: TableStatus) => ["tables", workspaceId, "list", status ?? "ALL"] as const,
  detail: (workspaceId: string, tableId: string) => ["tables", workspaceId, "table", tableId] as const,
  document: (workspaceId: string, workItemId: string) => ["tables", workspaceId, "document", workItemId] as const,
};

export const listTables = async (workspaceId: string, status?: TableStatus): Promise<TableList> =>
  (await apiClient.get<TableList>(`${ws(workspaceId)}/tables`, { params: status ? { status } : {} })).data;

export const getTable = async (workspaceId: string, tableId: string): Promise<TableDetail> =>
  (await apiClient.get<TableDetail>(one(workspaceId, tableId))).data;

export const correctCells = async (workspaceId: string, tableId: string, cells: readonly CellEdit[]): Promise<TableDetail> =>
  (await apiClient.patch<TableDetail>(`${one(workspaceId, tableId)}/cells`, { cells })).data;

export const setColumnRole = async (workspaceId: string, tableId: string, col: number, role: ColumnRole): Promise<TableDetail> =>
  (await apiClient.put<TableDetail>(`${one(workspaceId, tableId)}/columns/${col}/role`, { role })).data;

export const reviewTable = async (workspaceId: string, tableId: string, verdict: TableVerdict): Promise<TableDetail> =>
  (await apiClient.post<TableDetail>(`${one(workspaceId, tableId)}/review`, { verdict })).data;

export const getDocumentTables = async (workspaceId: string, workItemId: string): Promise<DocumentTables> =>
  (await apiClient.get<DocumentTables>(doc(workspaceId, workItemId))).data;

export const extractDocumentTables = async (workspaceId: string, workItemId: string, force = false): Promise<ExtractResult> =>
  (await apiClient.post<ExtractResult>(`${doc(workspaceId, workItemId)}/extract`, undefined, { params: { force } })).data;

const stem = (name: string): string => (name.replace(/\.[^.]+$/, "").replace(/[^A-Za-z0-9._-]+/g, "_") || "document").slice(0, 80);

/** Download one table (the export is authorised by the session, so it goes through the API client). */
export const downloadTable = async (
  workspaceId: string, tableId: string, format: ExportFormat, filename: string, ordinal: number,
): Promise<void> => {
  const response = await apiClient.get<Blob>(`${one(workspaceId, tableId)}/export`, {
    params: { format }, responseType: "blob", timeout: 120_000,
  });
  downloadBlob(response.data, `${stem(filename)}_table${ordinal + 1}.${format}`);
};

/** Every table of a document as one workbook (one sheet per table) or one JSON file. */
export const downloadDocumentTables = async (
  workspaceId: string, workItemId: string, format: "xlsx" | "json", filename: string,
): Promise<void> => {
  const response = await apiClient.get<Blob>(`${doc(workspaceId, workItemId)}/export`, {
    params: { format }, responseType: "blob", timeout: 120_000,
  });
  downloadBlob(response.data, `${stem(filename)}_tables.${format}`);
};
