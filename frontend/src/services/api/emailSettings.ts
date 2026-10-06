/**
 * ARCH40-S2:email-api. Workspace email: the override, and what resolves.
 *
 * Workspace-addressed since backend ARCH-01 Step 9c-2. See aiSettings.ts for
 * why the identifier is an explicit parameter.
 */

import apiClient from "./client";
import { SETTINGS_ENDPOINTS } from "./endpoints";

import type {
  EmailResolution,
  TestEmailRequest,
  TestEmailResponse,
  WorkspaceEmailOverride,
  WorkspaceEmailOverrideUpdate,
} from "@/types/emailSettings";

/** F-058: null when the workspace has no override (the normal state). */
export const getEmailSettings = async (
  workspaceId: string,
): Promise<WorkspaceEmailOverride | null> => {
  const response = await apiClient.get<WorkspaceEmailOverride | null>(
    SETTINGS_ENDPOINTS.emailSettings(workspaceId),
  );
  return response.data ?? null;
};

export const saveEmailSettings = async (
  workspaceId: string,
  payload: WorkspaceEmailOverrideUpdate,
): Promise<WorkspaceEmailOverride> => {
  const response = await apiClient.put<WorkspaceEmailOverride>(
    SETTINGS_ENDPOINTS.emailSettings(workspaceId),
    payload,
  );
  return response.data;
};

export const getEmailResolution = async (
  workspaceId: string,
): Promise<EmailResolution> => {
  const response = await apiClient.get<EmailResolution>(
    SETTINGS_ENDPOINTS.emailSettingsResolution(workspaceId),
  );
  return response.data;
};

export const testEmailSettings = async (
  workspaceId: string,
  payload: TestEmailRequest,
): Promise<TestEmailResponse> => {
  const response = await apiClient.post<TestEmailResponse>(
    SETTINGS_ENDPOINTS.emailSettingsTest(workspaceId),
    payload,
  );
  return response.data;
};
