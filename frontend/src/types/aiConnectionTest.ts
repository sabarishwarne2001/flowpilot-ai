/**
 * HARDENING-T1:D1 — mirrors `app/schemas/ai_connection_test.py`.
 *
 * A failed test is a normal response (success=false with a message and an
 * error code), not an HTTP error. Fields only a successful call can produce
 * are nullable.
 */
export interface TokenUsage {
  provider: string;
  model: string;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  estimated_cost: number;
}

export type ConnectionTestErrorCode =
  | "PROVIDER_UNSUPPORTED"
  | "PLATFORM_KEY_MISSING"
  | "CREDENTIAL_UNAVAILABLE"
  | "PROVIDER_REJECTED"
  | "PROVIDER_UNAVAILABLE"
  | "UNEXPECTED";

export interface AIConnectionTestResponse {
  success: boolean;
  provider: string;
  model: string;
  message: string;
  error_code: ConnectionTestErrorCode | null;
  latency_ms: number | null;
  response: string | null;
  token_usage: TokenUsage | null;
  credential_source: "TENANT" | "PLATFORM";
  resolution_origin: string;
}
