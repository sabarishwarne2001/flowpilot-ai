/**
 * ARCH-32 — Redaction Studio types.
 *
 * WHAT IS NOT HERE
 * ----------------
 * There is no field carrying the matched text, because there is no column
 * carrying it. `redaction_regions` stores an HMAC digest and never the token;
 * see the Step 1 migration header. A reviewer reads the value off the page
 * preview like any other reader of the document — the region list tells them
 * what KIND of thing was found and how confidently, not what it said.
 *
 * `tokenDigest` is a keyed MAC, exposed only so the studio can group regions
 * covering the same value ("appears on 4 pages") without displaying it.
 */

export type RedactionJobStatus =
  | "DETECTING"
  | "REVIEW"
  | "APPLYING"
  | "COMPLETED"
  | "FAILED"
  | "CANCELLED";

export type GeometryPrecision = "GLYPH" | "BLOCK" | "MANUAL";

export type RedactionProfileKey =
  | "all_identifiers"
  | "financial"
  | "hipaa_safe_harbor"
  | "india_kyc";

/** PDF points, origin BOTTOM-left. The canvas converts; nothing else should. */
export interface RedactionRegion {
  readonly id: string;
  readonly page_number: number;
  readonly x0: number;
  readonly y0: number;
  readonly x1: number;
  readonly y1: number;
  readonly detector: string;
  readonly confidence: number;
  readonly geometry_precision: GeometryPrecision;
  readonly enabled: boolean;
  readonly token_digest: string | null;
  readonly created_by_user_id: string | null;
  readonly toggled_by_user_id: string | null;
  readonly created_at: string;
  readonly checksum_validated: boolean;
}

export interface LeakCheck {
  readonly passed: boolean | null;
  /** The sentence §3.6 requires. Rendered as prose, never parsed. */
  readonly sentence: string | null;
  readonly detail: Record<string, unknown> | null;
}

export interface RedactionJob {
  readonly id: string;
  readonly work_item_id: string;
  readonly status: RedactionJobStatus;
  readonly profile_key: string;
  readonly render_dpi: number;
  readonly restore_text_layer: boolean;
  readonly page_count: number | null;
  readonly source_sha256: string;
  readonly output_sha256: string | null;
  readonly input_digest: string | null;
  readonly approved_by_user_id: string | null;
  readonly approved_at: string | null;
  readonly failure_reason: string | null;
  readonly created_at: string;
  readonly updated_at: string;
  readonly regions: readonly RedactionRegion[];
  readonly leak_check: LeakCheck;
  /** §3.9: true when this profile includes a name detector. */
  readonly names_limit_applies: boolean;
  readonly available_profiles: readonly string[];
}

export interface RedactionBundle {
  readonly document_url: string | null;
  readonly manifest_url: string | null;
  readonly output_sha256: string | null;
  readonly expires_in: number;
}

export const DETECTOR_LABELS: Readonly<Record<string, string>> = {
  card_number: "Card number",
  aadhaar: "Aadhaar",
  pan_india: "PAN",
  gstin: "GSTIN",
  us_ssn: "US SSN",
  iban: "IBAN",
  email: "Email address",
  phone: "Phone number",
  date_of_birth: "Date of birth",
  known_party: "Known party",
  manual: "Drawn by hand",
};

export const PROFILE_LABELS: Readonly<Record<string, string>> = {
  all_identifiers: "All identifiers",
  financial: "Financial identifiers",
  hipaa_safe_harbor: "HIPAA safe harbor",
  india_kyc: "India KYC",
};

/** G for glyph, B for block — the badge §3.6 specifies. */
export const precisionBadge = (precision: GeometryPrecision): string =>
  precision === "GLYPH" ? "G" : precision === "BLOCK" ? "B" : "M";

export const precisionTitle = (precision: GeometryPrecision): string =>
  precision === "GLYPH"
    ? "Placed on the matched characters"
    : precision === "BLOCK"
      ? "Widened to the whole line — this covers more than the match"
      : "Drawn by a reviewer";
