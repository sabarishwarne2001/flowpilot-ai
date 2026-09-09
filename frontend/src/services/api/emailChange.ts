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

/*
 * The emailChangeApi namespace wrapper was removed here, with its default export.
 *
 * It was an object literal re-exporting the named functions above, and it had
 * exactly two references in the whole repository: its own declaration and its
 * own `export default`. Every consumer imports the standalone functions
 * directly, which is why it could sit here for months looking like API
 * surface while being reachable from nothing.
 *
 * Safe to delete precisely because of that count -- anything importing it
 * would have appeared in the same grep that found it dead.
 */
