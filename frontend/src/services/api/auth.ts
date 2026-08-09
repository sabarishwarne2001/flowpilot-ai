import apiClient from "@/services/api/client";
import type {
  LoginRequest,
  RegisterRequest,
  PasswordActionResponse,
  ResendVerificationResponse,
  SessionResponse,
  TokenResponse,
  UserResponse,
  VerificationStatusResponse,
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
 * Submits a verification token read from the URL fragment.
 *
 * Sent in a POST body, never a query parameter. The token reached the browser
 * in the fragment (ARCH-03 §B.9), which no server sees; posting it back keeps
 * it out of access logs and Referer headers on the way in as well.
 *
 * Unauthenticated: the token is the proof, and the link usually opens in a
 * browser with no session.
 */
export const verifyEmailRequest = async (
  token: string
): Promise<VerificationStatusResponse> => {
  const response = await apiClient.post<VerificationStatusResponse>(
    "/auth/verify-email",
    { token }
  );

  return response.data;
};

/**
 * Requests a fresh verification link for the signed-in account.
 *
 * Takes no address on purpose. The endpoint only ever mails the address on the
 * session, which is what keeps it from answering "does this account exist" to
 * anyone who asks.
 */
export const resendVerificationRequest =
  async (): Promise<ResendVerificationResponse> => {
    const response = await apiClient.post<ResendVerificationResponse>(
      "/auth/resend-verification"
    );

    return response.data;
  };

/**
 * Requests a password reset link.
 *
 * Always resolves. The backend answers 202 whether or not the address matches
 * an account, and the UI must show the same message either way — surfacing a
 * difference here would rebuild the membership oracle the endpoint avoids.
 */
export const forgotPasswordRequest = async (
  email: string
): Promise<PasswordActionResponse> => {
  const response = await apiClient.post<PasswordActionResponse>(
    "/auth/forgot-password",
    { email }
  );

  return response.data;
};

/**
 * Completes a password reset with a token read from the URL fragment.
 *
 * Issues no session: the user signs in with the password they just chose.
 */
export const resetPasswordRequest = async (
  token: string,
  newPassword: string
): Promise<PasswordActionResponse> => {
  const response = await apiClient.post<PasswordActionResponse>(
    "/auth/reset-password",
    { token, new_password: newPassword }
  );

  return response.data;
};

/**
 * Replaces a known password.
 *
 * Returns a fresh access token, because the change revokes every session
 * including this one and then re-establishes this device alone. The caller
 * MUST store the returned token — the old one is already dead.
 */
export const changePasswordRequest = async (
  currentPassword: string,
  newPassword: string
): Promise<TokenResponse> => {
  const response = await apiClient.post<TokenResponse>(
    "/auth/change-password",
    { current_password: currentPassword, new_password: newPassword }
  );

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
  verifyEmailRequest,
  resendVerificationRequest,
  forgotPasswordRequest,
  resetPasswordRequest,
  changePasswordRequest,
  listSessionsRequest,
  revokeSessionRequest,
};
