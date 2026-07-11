/**
 * Authentication Data Transfer Objects (DTOs) for FlowPilot AI.
 *
 * These interfaces mirror the backend Pydantic request/response models
 * and provide strict compile-time contracts between the React frontend
 * and FastAPI backend.
 */

/* --------------------------------------------------------------------------
 * Request DTOs
 * -------------------------------------------------------------------------- */

/**
 * Login request payload.
 * Submitted as OAuth2 form data by the authentication service.
 */
export interface LoginRequest {
  readonly email: string;
  readonly password: string;
}

/**
 * User registration request payload.
 */
export interface RegisterRequest {
  readonly email: string;
  readonly password: string;
}

/* --------------------------------------------------------------------------
 * Response DTOs
 * -------------------------------------------------------------------------- */

/**
 * User object returned by the backend.
 */
export interface UserResponse {
  readonly id: string;
  readonly email: string;
  readonly is_active: boolean;
  readonly is_superuser: boolean;
  readonly created_at: string;
  readonly updated_at: string;
}

/**
 * OAuth2 access token response.
 */
export interface TokenResponse {
  readonly access_token: string;
  readonly token_type: "bearer";
}
/**
 * Internal authenticated session model.
 *
 * Used only by the frontend to persist the current
 * authenticated user and JWT access token.
 */
export interface AuthSessionPayload {
  readonly user: UserResponse;
  readonly token: string;
}
