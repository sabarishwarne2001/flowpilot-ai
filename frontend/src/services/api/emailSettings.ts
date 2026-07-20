import apiClient from "./client";

import type {
  EmailSettings,
  EmailSettingsCreate,
  TestEmailRequest,
  TestEmailResponse,
} from "@/types/emailSettings";

/* ============================================================================
 * API
 * ========================================================================== */

export const getEmailSettings = async (): Promise<EmailSettings> => {
  const response = await apiClient.get("/email-settings");
  return response.data;
};

export const saveEmailSettings = async (
  payload: EmailSettingsCreate,
): Promise<EmailSettings> => {
  const response = await apiClient.put(
    "/email-settings",
    payload,
  );

  return response.data;
};

export const testEmailSettings = async (
  payload: TestEmailRequest,
): Promise<TestEmailResponse> => {
  const response = await apiClient.post(
    "/email-settings/test",
    payload,
  );

  return response.data;
};
