/**
 * Shared Axios instance and interceptors for the FlowPilot AI frontend.
 *
 * ARCH-03 Step 7 changes two things, and both are load-bearing.
 *
 * withCredentials is now true. The refresh cookie is set by api.flowpilot.ai
 * and sent back to it, but every request from app.flowpilot.ai is
 * cross-ORIGIN even though it is same-SITE. A cross-origin XHR neither stores
 * nor sends cookies unless the request opts in with credentials AND the server
 * answers with Access-Control-Allow-Credentials and a concrete origin. With
 * withCredentials false the browser silently discards the Set-Cookie, login
 * appears to succeed, and refresh returns 401 forever with nothing in any log
 * to explain it. Same in development: localhost:3000 and localhost:8000 are
 * same-site but different origins.
 *
 * A 401 now attempts one refresh before giving up. Previously it cleared the
 * session immediately, which was right when the access token was the only
 * credential and is wrong now that a ten-minute expiry is the normal state of
 * affairs rather than the end of a session.
 */

import axios from "axios";

import type {
  AxiosError,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from "axios";

import { useAuthStore } from "@/store/useAuthStore";
import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ApiError, parseErrorEnvelope } from "@/services/api/errors";

export { ApiError } from "@/services/api/errors";
export type { ParsedApiError } from "@/services/api/errors";

const API_URL =
  import.meta.env.VITE_API_URL ?? "http://localhost:8000/api/v1";

/**
 * Routes that must never trigger a refresh attempt.
 *
 * /auth/refresh 401ing is the terminal answer, not a prompt to try again —
 * retrying it would recurse until the stack gave out. login and logout are
 * excluded because a 401 from them means what it says.
 */
const NO_REFRESH_PATHS = ["/auth/refresh", "/auth/login", "/auth/logout"];

const isRefreshExempt = (url: string | undefined): boolean =>
  !!url && NO_REFRESH_PATHS.some((path) => url.includes(path));

interface RetriableConfig extends InternalAxiosRequestConfig {
  _retried?: boolean;
}

export const apiClient = axios.create({
  baseURL: API_URL,
  timeout: 15000,
  withCredentials: true,
  headers: {
    "Content-Type": "application/json",
  },
});

apiClient.interceptors.request.use(
  (config: InternalAxiosRequestConfig): InternalAxiosRequestConfig => {
    const token = useAuthStore.getState().token;

    if (token) {
      config.headers.Authorization = `Bearer ${token}`;
    }

    return config;
  },

  (error: AxiosError) =>
    Promise.reject(
      new ApiError(
        error.message || "Failed to prepare request.",
        undefined,
        API_ERROR_CODES.NETWORK_ERROR,
      ),
    ),
);

/**
 * In-flight refresh, shared by every request that hits a 401 at once.
 *
 * A page that fires six parallel requests will get six 401s the moment the
 * access token expires. Without this, each would POST /auth/refresh: the first
 * rotates the token and the other five present one that has just been
 * superseded. The backend's grace window absorbs that, but it means five
 * needless rotations and five extra rows per expiry — and if the window were
 * ever shortened, five reuse alerts on a completely ordinary page load.
 *
 * One promise, awaited by all of them, one rotation.
 */
let refreshInFlight: Promise<string> | null = null;

const performRefresh = async (): Promise<string> => {
  // A bare axios call, not apiClient. Going through the instance would re-enter
  // these interceptors and, on failure, attempt to refresh the refresh.
  const response = await axios.post<{ access_token: string }>(
    `${API_URL}/auth/refresh`,
    null,
    { withCredentials: true, timeout: 15000 },
  );
  return response.data.access_token;
};

const refreshOnce = (): Promise<string> => {
  if (!refreshInFlight) {
    refreshInFlight = performRefresh().finally(() => {
      refreshInFlight = null;
    });
  }
  return refreshInFlight;
};

apiClient.interceptors.response.use(
  (response) => response,

  async (error: AxiosError<unknown>) => {
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
    const config = error.config as RetriableConfig | undefined;

    const parsed = parseErrorEnvelope(
      error.response.data,
      status,
      error.message || "An unexpected server error occurred.",
    );

    if (
      status === 401 &&
      config &&
      !config._retried &&
      !isRefreshExempt(config.url)
    ) {
      config._retried = true;

      try {
        const token = await refreshOnce();
        useAuthStore.getState().setToken(token);
        config.headers.Authorization = `Bearer ${token}`;
        return apiClient(config as AxiosRequestConfig);
      } catch {
        // The refresh cookie is gone, expired, or was revoked — including the
        // reuse-detection case, where the server has already signed this
        // device out on purpose. Nothing further to try.
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
    }

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

/**
 * Restores a session from the refresh cookie on application start.
 *
 * The access token no longer survives a reload — it lives in memory only. What
 * survives is the HttpOnly cookie, which the browser will present here. A
 * successful call means this browser still holds a valid session and the user
 * should not see a login screen.
 *
 * Returns the access token, or null if there is no session to restore. Never
 * throws: "not signed in" is an ordinary answer at startup, not an error.
 */
export const restoreSession = async (): Promise<string | null> => {
  try {
    const token = await refreshOnce();
    useAuthStore.getState().setToken(token);
    return token;
  } catch {
    useAuthStore.getState().clearAuth();
    return null;
  }
};

export default apiClient;
