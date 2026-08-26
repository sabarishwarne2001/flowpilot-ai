import apiClient from "@/services/api/client";

export interface AuditLogRead {
  readonly id: string;
  readonly created_at: string;
  readonly organization_id: string;
  readonly workspace_id: string | null;
  readonly actor_id: string | null;
  readonly api_key_id: string | null;
  readonly resource_type: string;
  readonly resource_id: string | null;
  readonly action: string;
  readonly outcome: string;
  readonly details: Record<string, unknown> | null;
  readonly ip_address: string | null;
  readonly user_agent: string | null;
}

export interface AuditLogPage {
  readonly items: readonly AuditLogRead[];
  readonly limit: number;
  readonly has_more: boolean;
  readonly next_cursor: string | null;
}

export interface AuditLogQuery {
  readonly limit?: number;
  readonly cursor?: string | null;
  readonly resource_type?: string;
  readonly action?: string;
  readonly outcome?: string;
  readonly actor_id?: string;
  readonly api_key_id?: string;
  readonly resource_id?: string;
  readonly workspace_id?: string;
  readonly date_from?: string;
  readonly date_to?: string;
}

export type AuditExportFormat = "CSV" | "NDJSON";

const seg = (value: string): string => encodeURIComponent(value);

const org = (organizationId: string): string => {
  if (!organizationId) {
    throw new Error(
      "An organizationId is required to build this URL. Gate the query with `enabled: Boolean(organizationId)`.",
    );
  }
  return seg(organizationId);
};

export const AUDIT_ENDPOINTS = {
  list: (organizationId: string) =>
    `/organizations/${org(organizationId)}/audit-logs`,
  detail: (organizationId: string, auditLogId: string) =>
    `/organizations/${org(organizationId)}/audit-logs/${seg(auditLogId)}`,
  export: (organizationId: string) =>
    `/organizations/${org(organizationId)}/audit-logs/export`,
} as const;

const clean = (query: AuditLogQuery): Record<string, string | number> => {
  const params: Record<string, string | number> = {};
  Object.entries(query).forEach(([key, value]) => {
    if (value === undefined || value === null || value === "") {
      return;
    }
    params[key] = value as string | number;
  });
  return params;
};

export const listAuditLogs = async (
  organizationId: string,
  query: AuditLogQuery = {},
): Promise<AuditLogPage> => {
  const response = await apiClient.get<AuditLogPage>(
    AUDIT_ENDPOINTS.list(organizationId),
    { params: clean(query) },
  );
  return response.data;
};

export const getAuditLog = async (
  organizationId: string,
  auditLogId: string,
): Promise<AuditLogRead> => {
  const response = await apiClient.get<AuditLogRead>(
    AUDIT_ENDPOINTS.detail(organizationId, auditLogId),
  );
  return response.data;
};

export const exportAuditLogs = async (
  organizationId: string,
  format: AuditExportFormat,
  query: AuditLogQuery = {},
): Promise<Blob> => {
  const response = await apiClient.get<Blob>(
    AUDIT_ENDPOINTS.export(organizationId),
    {
      params: { ...clean(query), format },
      responseType: "blob",
      timeout: 300_000,
    },
  );
  return response.data;
};

export const downloadBlob = (blob: Blob, filename: string): void => {
  const url = URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = filename;
    document.body.appendChild(anchor);
    anchor.click();
    anchor.remove();
  } finally {
    setTimeout(() => URL.revokeObjectURL(url), 10_000);
  }
};

export const auditApi = {
  listAuditLogs,
  getAuditLog,
  exportAuditLogs,
  downloadBlob,
} as const;

export default auditApi;
