import apiClient from "@/services/api/client";
import type {
  LoginRequest,
  RegisterRequest,
  SessionResponse,
  TokenResponse,
  UserResponse,
} from "@/types/auth";

/**
 * Registers a new user account.
 */
export const registerRequest = async (
  data: RegisterRequest
): Promise<UserResponse> => {
  const response = await apiClient.post<UserResponse>("/auth/register", data, {
    headers: {
      Accept: "application/json",
    },
  });

  return response.data;
};

/**
 * Authenticates a user using FastAPI OAuth2 form-urlencoded credentials.
 */
export const loginRequest = async (
  data: LoginRequest
): Promise<TokenResponse> => {
  const formData = new URLSearchParams({
    username: data.email.trim(),
    password: data.password,
  });

  const response = await apiClient.post<TokenResponse>(
    "/auth/login",
    formData,
    {
      headers: {
        "Content-Type": "application/x-www-form-urlencoded",
        Accept: "application/json",
      },
    }
  );

  return response.data;
};
/**
 * Returns the currently authenticated user's profile.
 */
export const getMeRequest = async (): Promise<UserResponse> => {
  const response = await apiClient.get<UserResponse>("/auth/me", {
    headers: {
      Accept: "application/json",
    },
  });

  return response.data;
};

/**
 * Ends this session on the server and clears the refresh cookie.
 *
 * Never rejects. A sign-out that fails because the network is down, or because
 * the access token had already expired, must still clear local state — leaving
 * the user on an authenticated screen after they asked to leave is worse than
 * a session row that outlives its usefulness. The backend route is
 * unauthenticated and if-empty idempotent for the same reason.
 */
export const logoutRequest = async (): Promise<void> => {
  try {
    await apiClient.post("/auth/logout");
  } catch {
    // Deliberately swallowed. See above.
  }
};

/**
 * Ends every session on every device.
 *
 * Authenticated, unlike logoutRequest: this acts on sessions the caller is not
 * holding. Offered from account settings and used after a password change.
 */
export const logoutAllRequest = async (): Promise<void> => {
  await apiClient.post("/auth/logout-all");
};

/**
 * Lists this account's live sessions — the device list.
 */
export const listSessionsRequest = async (): Promise<SessionResponse[]> => {
  const response = await apiClient.get<SessionResponse[]>("/auth/sessions");
  return response.data;
};

/**
 * Ends one session from the device list.
 */
export const revokeSessionRequest = async (
  sessionId: string
): Promise<void> => {
  await apiClient.delete(`/auth/sessions/${sessionId}`);
};

/**
 * Retained for backwards compatibility with existing call sites.
 */
export const logout = logoutRequest;

/**
 * Unified authentication API surface.
 *
 * Keeps all authentication operations grouped together,
 * making future dependency injection and testing easier.
 */
export const authApi = {
  registerRequest,
  loginRequest,
  getMeRequest,
  logout,
  logoutRequest,
  logoutAllRequest,
  listSessionsRequest,
  revokeSessionRequest,
};
