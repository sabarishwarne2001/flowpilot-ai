/** ARCH43-S2:api-cases — Case Intelligence. Workspace routes are gated on capability.case_intelligence. */
import { apiClient } from "@/services/api/client";
import type {
  CaseDetail, CaseList, CaseStatus, PublicRequestInfo, PublicUploadResult, RequestCreated, RequestRow, TemplateRow, TemplateWrite,
} from "@/types/cases";

const ws = (workspaceId: string): string => `/workspaces/${encodeURIComponent(workspaceId)}`;
const one = (workspaceId: string, caseId: string): string => `${ws(workspaceId)}/cases/${encodeURIComponent(caseId)}`;

export const caseKeys = {
  all: (workspaceId: string) => ["cases", workspaceId] as const,
  templates: (workspaceId: string) => ["cases", workspaceId, "templates"] as const,
  detail: (workspaceId: string, caseId: string) => ["cases", workspaceId, "case", caseId] as const,
  splits: (workspaceId: string) => ["packet-splits", workspaceId] as const,
  split: (workspaceId: string, splitId: string) => ["packet-splits", workspaceId, splitId] as const,
};

export const listCaseTemplates = async (workspaceId: string): Promise<TemplateRow[]> =>
  (await apiClient.get<TemplateRow[]>(`${ws(workspaceId)}/case-templates`)).data;
export const createCaseTemplate = async (workspaceId: string, body: TemplateWrite): Promise<TemplateRow> =>
  (await apiClient.post<TemplateRow>(`${ws(workspaceId)}/case-templates`, body)).data;
export const publishCaseTemplate = async (workspaceId: string, templateId: string): Promise<TemplateRow> =>
  (await apiClient.post<TemplateRow>(`${ws(workspaceId)}/case-templates/${encodeURIComponent(templateId)}/publish`)).data;
export const retireCaseTemplate = async (workspaceId: string, templateId: string): Promise<TemplateRow> =>
  (await apiClient.post<TemplateRow>(`${ws(workspaceId)}/case-templates/${encodeURIComponent(templateId)}/retire`)).data;

export const listCases = async (workspaceId: string, status?: CaseStatus): Promise<CaseList> =>
  (await apiClient.get<CaseList>(`${ws(workspaceId)}/cases`, { params: status ? { status } : {} })).data;
export const createCase = async (workspaceId: string, templateId: string, title: string): Promise<CaseDetail> =>
  (await apiClient.post<CaseDetail>(`${ws(workspaceId)}/cases`, { template_id: templateId, title })).data;
export const getCase = async (workspaceId: string, caseId: string): Promise<CaseDetail> =>
  (await apiClient.get<CaseDetail>(one(workspaceId, caseId))).data;
export const addCaseDocument = async (workspaceId: string, caseId: string, workItemId: string, documentType?: string): Promise<CaseDetail> =>
  (await apiClient.post<CaseDetail>(`${one(workspaceId, caseId)}/documents`, { work_item_id: workItemId, document_type: documentType || null })).data;
export const removeCaseDocument = async (workspaceId: string, caseId: string, workItemId: string): Promise<CaseDetail> =>
  (await apiClient.delete<CaseDetail>(`${one(workspaceId, caseId)}/documents/${encodeURIComponent(workItemId)}`)).data;
export const evaluateCase = async (workspaceId: string, caseId: string): Promise<CaseDetail> =>
  (await apiClient.post<CaseDetail>(`${one(workspaceId, caseId)}/evaluate`)).data;
export const closeCase = async (workspaceId: string, caseId: string): Promise<CaseDetail> =>
  (await apiClient.post<CaseDetail>(`${one(workspaceId, caseId)}/close`)).data;
export const requestDocument = async (workspaceId: string, caseId: string, documentType: string, recipientLabel: string): Promise<RequestCreated> =>
  (await apiClient.post<RequestCreated>(`${one(workspaceId, caseId)}/requests`, { document_type: documentType, recipient_label: recipientLabel })).data;
export const revokeDocumentRequest = async (workspaceId: string, caseId: string, requestId: string): Promise<RequestRow> =>
  (await apiClient.post<RequestRow>(`${one(workspaceId, caseId)}/requests/${encodeURIComponent(requestId)}/revoke`)).data;

export const previewDocumentRequest = async (token: string): Promise<PublicRequestInfo> =>
  (await apiClient.get<PublicRequestInfo>(`/public/document-requests/${encodeURIComponent(token)}`)).data;
export const uploadRequestedDocument = async (token: string, file: File): Promise<PublicUploadResult> => {
  const form = new FormData();
  form.append("file", file);
  return (await apiClient.post<PublicUploadResult>(`/public/document-requests/${encodeURIComponent(token)}`, form)).data;
};
