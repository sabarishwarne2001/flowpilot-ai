import axios from "axios";

import type {
  AxiosError,
  InternalAxiosRequestConfig,
} from "axios";

import { useAuthStore } from "@/store/useAuthStore";

/**
 * Standardized API error used across the FlowPilot AI frontend.
 * Wraps transport and backend errors into a consistent application error model.
 */
export class ApiError extends Error {
  public readonly status?: number;

  public readonly code?: string;

  public readonly detail?: string;

  constructor(
    message: string,
    status?: number,
    code?: string,
    detail?: string,
  ) {
    super(message);

    this.name = "ApiError";

    if (status !== undefined) {
      this.status = status;
    }

    if (code !== undefined) {
      this.code = code;
    }

    if (detail !== undefined) {
      this.detail = detail;
    }

    Object.setPrototypeOf(this, ApiError.prototype);
  }
}

/**
 * Base backend API URL.
 * Falls back to localhost during local development.
 */
const API_URL =
  import.meta.env.VITE_API_URL ??
  "http://localhost:8000/api/v1";

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
  (
    config: InternalAxiosRequestConfig,
  ): InternalAxiosRequestConfig => {
    const token =
      useAuthStore.getState().token;

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },

  (error: AxiosError) => {
    return Promise.reject(
      new ApiError(
        error.message ||
          "Failed to prepare request.",
      ),
    );
  },
);

/**
 * Centralized API response error handling.
 */
apiClient.interceptors.response.use(
  (response) => response,

  (error: AxiosError<unknown>) => {
    if (!error.response) {
      return Promise.reject(
        new ApiError(
          "Unable to reach the server.",
          undefined,
          "NETWORK_ERROR",
        ),
      );
    }

    const status = error.response.status;

    const data =
      error.response.data as
        | {
            detail?: string;
            code?: string;
          }
        | undefined;

    const detail =
      typeof data?.detail === "string"
        ? data.detail
        : error.message;

    if (status === 401) {
      useAuthStore
        .getState()
        .clearAuth();

      return Promise.reject(
        new ApiError(
          "Session expired. Please sign in again.",
          401,
          "UNAUTHORIZED",
          detail,
        ),
      );
    }

    return Promise.reject(
      new ApiError(
        detail ||
          "An unexpected server error occurred.",
        status,
        data?.code ?? "SERVER_ERROR",
        detail,
      ),
    );
  },
);

export default apiClient;
