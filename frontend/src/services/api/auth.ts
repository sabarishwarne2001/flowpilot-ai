import apiClient from "@/services/api/client";
import type {
  LoginRequest,
  RegisterRequest,
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
 * Client-side logout placeholder.
 *
 * Reserved for future backend logout,
 * refresh-token revocation, or audit logging.
 */
export const logout = async (): Promise<void> => {
  return Promise.resolve();
};
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
};
