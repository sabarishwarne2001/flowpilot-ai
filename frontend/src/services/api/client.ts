/**
 * Shared Axios instance and interceptors for the FlowPilot AI frontend.
 *
 * The response interceptor now resolves every backend error envelope through
 * parseErrorEnvelope. Previously it read only `detail`, which the ARCH-01
 * domain handler does not emit — so every tenancy error message was silently
 * replaced by axios's own "Request failed with status code N", and the
 * carefully authored backend prose never reached the user.
 *
 * ApiError is re-exported here for backwards compatibility: it moved to
 * ./errors so the parser could stay free of axios and store imports, and every
 * existing `import { ApiError } from "@/services/api/client"` keeps working.
 */

import axios from "axios";

import type { AxiosError, InternalAxiosRequestConfig } from "axios";

import { useAuthStore } from "@/store/useAuthStore";
import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ApiError, parseErrorEnvelope } from "@/services/api/errors";

export { ApiError } from "@/services/api/errors";
export type { ParsedApiError } from "@/services/api/errors";

/**
 * Base backend API URL.
 * Falls back to localhost during local development.
 */
const API_URL =
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

/**
 * Shared Axios instance used throughout the application.
 */
export const apiClient = axios.create({
  baseURL: API_URL,
  timeout: 15000,
  withCredentials: false,
  headers: {
    "Content-Type": "application/json",
  },
});

/**
 * Automatically inject JWT bearer token into every authenticated request.
 */
apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig): InternalAxiosRequestConfig => {
    const token = useAuthStore.getState().token;

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },

  (error: AxiosError) => {
    return Promise.reject(
      new ApiError(
        error.message || "Failed to prepare request.",
        undefined,
        API_ERROR_CODES.NETWORK_ERROR,
      ),
    );
  },
);

/**
 * Centralized API response error handling.
 *
 * Every rejection is an ApiError carrying a stable `code`. Callers branch on
 * the code and display the message; they must never parse the message, which
 * the backend may reword at any time.
 */
apiClient.interceptors.response.use(
  (response) => response,

  (error: AxiosError<unknown>) => {
    if (!error.response) {
      return Promise.reject(
        new ApiError(
          "Unable to reach the server.",
          undefined,
          API_ERROR_CODES.NETWORK_ERROR,
        ),
      );
    }

    const status = error.response.status;

    const parsed = parseErrorEnvelope(
      error.response.data,
      status,
      error.message || "An unexpected server error occurred.",
    );

    // A 401 means the session is gone, not merely insufficient. Clearing here
    // guarantees no component can act on a token the server has rejected.
    //
    // This is the first half of the fix for the expired-token defect: the
    // second half is the guard in Step 6, which routes UNAUTHORIZED to /login
    // rather than treating a failed request as "this user has no workspace".
    if (status === 401) {
      useAuthStore.getState().clearAuth();

      return Promise.reject(
        new ApiError(
          "Session expired. Please sign in again.",
          401,
          API_ERROR_CODES.UNAUTHORIZED,
          parsed.message,
          parsed.details,
        ),
      );
    }

    return Promise.reject(
      new ApiError(
        parsed.message,
        status,
        parsed.code,
        parsed.message,
        parsed.details,
      ),
    );
  },
);

export default apiClient;
