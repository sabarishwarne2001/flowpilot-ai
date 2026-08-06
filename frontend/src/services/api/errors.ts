/**
 * Error model and response-envelope parsing for the FlowPilot AI API.
 *
 * Separated from client.ts deliberately. The parser is pure — it imports no
 * axios and no store — so it can be reasoned about and tested in isolation,
 * and so the auth store is not pulled into every module that only needs to
 * catch an ApiError.
 *
 * The backend emits three distinct error envelopes, and the client meets all
 * three:
 *
 *   1. Domain errors, from app/core/exception_handlers.py:
 *        { "code": "LAST_OWNER", "message": "...", "details": {} }
 *
 *   2. HTTPException, still used by auth.py and upload.py:
 *        { "detail": "Could not validate credentials" }
 *
 *   3. FastAPI request validation, on any malformed body:
 *        { "detail": [{ "loc": [...], "msg": "...", "type": "..." }] }
 *
 * Handling only the first would break authentication messages; handling only
 * the second discards every domain message the backend authors. The
 * pre-ARCH-01 client read `detail` alone, so every tenancy error surfaced as
 * axios's own "Request failed with status code 409".
 */

import { API_ERROR_CODES } from "@/constants/errorCodes";

/**
 * Standardized API error used across the FlowPilot AI frontend.
 *
 * `code` is the branching contract. `message` is display prose and may be
 * reworded by the backend without notice, so never branch on it.
 */
export class ApiError extends Error {
  public readonly status?: number;

  public readonly code?: string;

  public readonly detail?: string;

  /** Structured context from the domain envelope. Empty for other shapes. */
  public readonly details: Readonly<Record<string, unknown>>;

  constructor(
    message: string,
    status?: number,
    code?: string,
    detail?: string,
    details?: Record<string, unknown>,
  ) {
    super(message);

    this.name = "ApiError";
    this.details = details ?? {};

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

  /** True when this error carries the given code. */
  public is(code: string): boolean {
    return this.code === code;
  }

  /** True when this error's code appears in the given set. */
  public isOneOf(codes: ReadonlySet<string>): boolean {
    return this.code !== undefined && codes.has(this.code);
  }
}

/* ==========================================================================
 * Envelope parsing
 * ========================================================================== */

interface DomainEnvelope {
  code: string;
  message: string;
  details?: Record<string, unknown>;
}

interface DetailEnvelope {
  detail: string;
}

interface ValidationIssue {
  loc?: unknown[];
  msg?: string;
  type?: string;
}

interface ValidationEnvelope {
  detail: ValidationIssue[];
}

export interface ParsedApiError {
  message: string;
  code: string;
  details: Record<string, unknown>;
}

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const isDomainEnvelope = (value: unknown): value is DomainEnvelope =>
  isRecord(value) &&
  typeof value.code === "string" &&
  typeof value.message === "string";

const isDetailEnvelope = (value: unknown): value is DetailEnvelope =>
  isRecord(value) && typeof value.detail === "string";

const isValidationEnvelope = (value: unknown): value is ValidationEnvelope =>
  isRecord(value) && Array.isArray(value.detail);

/**
 * Formats FastAPI's validation issues into a single readable line.
 *
 * The `loc` array begins with the request part ("body", "query", "path"),
 * which is noise to a user, so it is dropped and only the field path is kept.
 */
const formatValidationIssues = (issues: ValidationIssue[]): string => {
  const formatted = issues
    .map((issue) => {
      const field = Array.isArray(issue.loc)
        ? issue.loc
            .slice(1)
            .filter((part) => typeof part === "string" || typeof part === "number")
            .join(".")
        : "";
      const msg = typeof issue.msg === "string" ? issue.msg : "is invalid";
      return field ? `${field}: ${msg}` : msg;
    })
    .filter(Boolean);

  return formatted.length > 0
    ? formatted.join("; ")
    : "The submitted data is invalid.";
};

/**
 * Resolves any backend error body into a message, a stable code, and context.
 *
 * Pure: no side effects, no imports beyond the code constants. Exported so the
 * parsing contract can be verified without instantiating axios.
 *
 * @param body - The parsed response body, whatever shape it arrived in.
 * @param status - HTTP status, used to pick a sensible fallback code.
 * @param fallbackMessage - Used only when the body yields nothing readable.
 */
export const parseErrorEnvelope = (
  body: unknown,
  status: number,
  fallbackMessage: string,
): ParsedApiError => {
  // 1. Domain envelope — the ARCH-01 contract. Checked first because it is
  //    the shape every tenancy, invitation, and slug error uses.
  if (isDomainEnvelope(body)) {
    return {
      message: body.message,
      code: body.code,
      details: isRecord(body.details) ? body.details : {},
    };
  }

  // 2. FastAPI request validation. Checked before the string form because
  //    both use the key `detail`.
  if (isValidationEnvelope(body)) {
    return {
      message: formatValidationIssues(body.detail),
      code: API_ERROR_CODES.VALIDATION_ERROR,
      details: { issues: body.detail },
    };
  }

  // 3. HTTPException. Still emitted by auth.py and upload.py.
  if (isDetailEnvelope(body)) {
    return {
      message: body.detail,
      code:
        status === 401
          ? API_ERROR_CODES.UNAUTHORIZED
          : API_ERROR_CODES.BAD_REQUEST,
      details: {},
    };
  }

  // 4. Unrecognised. Report the status honestly rather than inventing a cause.
  return {
    message: fallbackMessage,
    code:
      status >= 500
        ? API_ERROR_CODES.SERVER_ERROR
        : API_ERROR_CODES.BAD_REQUEST,
    details: {},
  };
};
