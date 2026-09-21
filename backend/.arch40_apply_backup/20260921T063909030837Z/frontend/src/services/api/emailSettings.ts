/**
 * Email settings API service for FlowPilot AI.
 *
 * Workspace-addressed since backend ARCH-01 Step 9c-2. See aiSettings.ts for
 * why the identifier is an explicit parameter.
 */

import apiClient from "./client";
import { SETTINGS_ENDPOINTS } from "./endpoints";

import type {
  EmailSettings,
  EmailSettingsCreate,
  TestEmailRequest,
  TestEmailResponse,
} from "@/types/emailSettings";

/* ============================================================================
 * API
 * ========================================================================== */

export const getEmailSettings = async (
  workspaceId: string,
): Promise<EmailSettings> => {
  const response = await apiClient.get(
    SETTINGS_ENDPOINTS.emailSettings(workspaceId),
  );
  return response.data;
};

export const saveEmailSettings = async (
  workspaceId: string,
  payload: EmailSettingsCreate,
): Promise<EmailSettings> => {
  const response = await apiClient.put(
    SETTINGS_ENDPOINTS.emailSettings(workspaceId),
    payload,
  );

  return response.data;
};

export const testEmailSettings = async (
  workspaceId: string,
  payload: TestEmailRequest,
): Promise<TestEmailResponse> => {
  const response = await apiClient.post(
    SETTINGS_ENDPOINTS.emailSettingsTest(workspaceId),
    payload,
  );

  return response.data;
};
