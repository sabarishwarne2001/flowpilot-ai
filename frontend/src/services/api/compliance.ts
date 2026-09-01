import apiClient from "@/services/api/client";
import { COMPLIANCE_ENDPOINTS } from "@/services/api/endpoints";
import type {
  ComplianceExport,
  ComplianceExportDownload,
  ComplianceOverview,
  DataResidency,
  DataResidencyUpdateRequest,
  ErasedSubject,
  ErasurePreview,
  ErasureRequestPayload,
  ErasureResult,
  RetentionPolicy,
  RetentionPolicyUpdateRequest,
} from "@/types/compliance";

const JSON_HEADERS = { Accept: "application/json" } as const;

export const getComplianceOverview = async (
  organizationId: string,
): Promise<ComplianceOverview> => {
  const response = await apiClient.get<ComplianceOverview>(
    COMPLIANCE_ENDPOINTS.overview(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getDataResidency = async (
  organizationId: string,
): Promise<DataResidency> => {
  const response = await apiClient.get<DataResidency>(
    COMPLIANCE_ENDPOINTS.residency(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateDataResidency = async (
  organizationId: string,
  payload: DataResidencyUpdateRequest,
): Promise<DataResidency> => {
  const response = await apiClient.put<DataResidency>(
    COMPLIANCE_ENDPOINTS.residency(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const getRetentionPolicy = async (
  organizationId: string,
): Promise<RetentionPolicy> => {
  const response = await apiClient.get<RetentionPolicy>(
    COMPLIANCE_ENDPOINTS.retention(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const updateRetentionPolicy = async (
  organizationId: string,
  payload: RetentionPolicyUpdateRequest,
): Promise<RetentionPolicy> => {
  const response = await apiClient.put<RetentionPolicy>(
    COMPLIANCE_ENDPOINTS.retention(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listErasures = async (
  organizationId: string,
): Promise<ErasedSubject[]> => {
  const response = await apiClient.get<ErasedSubject[]>(
    COMPLIANCE_ENDPOINTS.erasures(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const previewErasure = async (
  organizationId: string,
  subjectUserId: string,
): Promise<ErasurePreview> => {
  const response = await apiClient.get<ErasurePreview>(
    COMPLIANCE_ENDPOINTS.erasurePreview(organizationId),
    {
      params: { subject_user_id: subjectUserId },
      headers: JSON_HEADERS,
    },
  );
  return response.data;
};

export const createErasure = async (
  organizationId: string,
  payload: ErasureRequestPayload,
): Promise<ErasureResult> => {
  const response = await apiClient.post<ErasureResult>(
    COMPLIANCE_ENDPOINTS.erasures(organizationId),
    payload,
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const listComplianceExports = async (
  organizationId: string,
): Promise<ComplianceExport[]> => {
  const response = await apiClient.get<ComplianceExport[]>(
    COMPLIANCE_ENDPOINTS.exports(organizationId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};

export const createComplianceExport = async (
  organizationId: string,
): Promise<ComplianceExport> => {
  const response = await apiClient.post<ComplianceExport>(
    COMPLIANCE_ENDPOINTS.exports(organizationId),
    {},
    { headers: JSON_HEADERS },
  );
  return response.data;
};

/**
 * Fetches a short-lived URL, then hands it to the browser.
 *
 * Deliberately not stored in component state or in a query cache. The URL is
 * a bearer credential with a 15-minute life; caching one means handing out an
 * expired link on the second click and keeping a live credential in memory in
 * between.
 */
export const getComplianceExportDownloadUrl = async (
  organizationId: string,
  exportId: string,
): Promise<ComplianceExportDownload> => {
  const response = await apiClient.get<ComplianceExportDownload>(
    COMPLIANCE_ENDPOINTS.exportDownload(organizationId, exportId),
    { headers: JSON_HEADERS },
  );
  return response.data;
};
