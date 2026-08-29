import apiClient from "@/services/api/client";
import { EMAIL_CHANGE_ENDPOINTS } from "@/services/api/endpoints";
import type {
  EmailChangeConfirmResult,
  EmailChangeRequestPayload,
  EmailChangeRequestResult,
} from "@/types/profile";

export const requestEmailChange = async (
  data: EmailChangeRequestPayload,
): Promise<EmailChangeRequestResult> => {
  const response = await apiClient.post<EmailChangeRequestResult>(
    EMAIL_CHANGE_ENDPOINTS.request,
    data,
  );
  return response.data;
};

export const cancelEmailChange = async (): Promise<void> => {
  await apiClient.delete(EMAIL_CHANGE_ENDPOINTS.request);
};

export const confirmEmailChange = async (
  token: string,
): Promise<EmailChangeConfirmResult> => {
  const response = await apiClient.post<EmailChangeConfirmResult>(
    EMAIL_CHANGE_ENDPOINTS.confirm,
    { token },
  );
  return response.data;
};

export const emailChangeApi = {
  requestEmailChange,
  cancelEmailChange,
  confirmEmailChange,
} as const;

export default emailChangeApi;
