import apiClient from "@/services/api/client";
import { ORG_EMAIL_ENDPOINTS } from "@/services/api/endpoints";
import type {
  OrganizationEmailSettings,
  OrganizationEmailSettingsUpdate,
  OrganizationEmailTestResult,
} from "@/types/organizationEmail";

export const getOrganizationEmailSettings = async (
  organizationId: string,
): Promise<OrganizationEmailSettings> => {
  const response = await apiClient.get<OrganizationEmailSettings>(
    ORG_EMAIL_ENDPOINTS.settings(organizationId),
  );
  return response.data;
};

export const updateOrganizationEmailSettings = async (
  organizationId: string,
  data: OrganizationEmailSettingsUpdate,
): Promise<OrganizationEmailSettings> => {
  const response = await apiClient.patch<OrganizationEmailSettings>(
    ORG_EMAIL_ENDPOINTS.settings(organizationId),
    data,
  );
  return response.data;
};

export const testOrganizationEmailSettings = async (
  organizationId: string,
  recipient: string,
): Promise<OrganizationEmailTestResult> => {
  const response = await apiClient.post<OrganizationEmailTestResult>(
    ORG_EMAIL_ENDPOINTS.test(organizationId),
    { recipient },
  );
  return response.data;
};

export const organizationEmailApi = {
  getOrganizationEmailSettings,
  updateOrganizationEmailSettings,
  testOrganizationEmailSettings,
} as const;

export default organizationEmailApi;
