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
  // Exposed so the client can render the verification banner and route
  // around the tenant gate before the server has to refuse a request.
  readonly email_verified_at: string | null;
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

/**
 * One live refresh session, as returned by GET /auth/sessions.
 *
 * Carries no token and no hash — a device list is a read-only view, and
 * anything replayable in it would turn the screen that shows a user their
 * sessions into the page that gives them away.
 */
export interface SessionResponse {
  readonly id: string;
  readonly created_at: string;
  readonly expires_at: string;
  readonly last_used_at: string | null;
  readonly ip_address: string | null;
  readonly user_agent: string | null;
}

/**
 * Outcome of a verification attempt.
 */
export interface VerificationStatusResponse {
  readonly email: string;
  readonly email_verified_at: string;
  readonly already_verified: boolean;
}

/**
 * Acknowledgement of a resend request.
 *
 * delivered is false when the message could not be sent. The request still
 * succeeded — an SMTP outage must not present as a broken account.
 */
export interface ResendVerificationResponse {
  readonly delivered: boolean;
  readonly detail: string;
}

/**
 * Acknowledgement of a password action.
 *
 * sessions_revoked is reported so the client can say what just happened
 * rather than leaving a user to discover their other devices are signed out.
 */
export interface PasswordActionResponse {
  readonly detail: string;
  readonly sessions_revoked: boolean;
}
