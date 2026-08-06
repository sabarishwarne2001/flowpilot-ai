import { z } from "zod";

/**
 * Validation schema for tenant provisioning.
 *
 * Mirrors OrganizationCreate in app/schemas/organization.py. Bounds are
 * duplicated rather than imported because they belong to the API contract, not
 * to a shared library — and a client that silently accepted a value the server
 * rejects would surface the failure as an opaque 422 after submission instead
 * of inline feedback while typing.
 */

/** DNS label limit. Slugs must stay usable as subdomains. */
export const MAX_SLUG_LENGTH = 63;

/** Two characters permits "hr" or "qa" while rejecting single-character noise. */
export const MIN_SLUG_LENGTH = 2;

/**
 * Canonical slug grammar: starts and ends alphanumeric, single internal
 * hyphens. Matches _SLUG_PATTERN in app/core/slugs.py.
 */
export const SLUG_PATTERN = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

/**
 * Derives a slug from a display name.
 *
 * Mirrors slugify() in app/core/slugs.py: NFKD-normalise, strip diacritics,
 * collapse unsupported runs into single hyphens, trim, truncate.
 *
 * Advisory only. The server derives its own slug when none is supplied and
 * resolves collisions, so this exists to show the user what their URL will
 * look like — not to decide it.
 */
export const deriveSlug = (value: string): string => {
  if (!value) {
    return "";
  }

  const ascii = value
    .normalize("NFKD")
    // eslint-disable-next-line no-misleading-character-class
    .replace(/[\u0300-\u036f]/g, "");

  const hyphenated = ascii
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");

  return hyphenated.length > MAX_SLUG_LENGTH
    ? hyphenated.slice(0, MAX_SLUG_LENGTH).replace(/-+$/, "")
    : hyphenated;
};

export const createOrganizationSchema = z.object({
  organization_name: z
    .string()
    .trim()
    .min(1, "Organization name is required.")
    .max(150, "Organization name cannot exceed 150 characters."),

  workspace_name: z
    .string()
    .trim()
    .max(100, "Workspace name cannot exceed 100 characters.")
    .optional()
    .or(z.literal("")),

  organization_slug: z
    .string()
    .trim()
    .min(MIN_SLUG_LENGTH, `Identifier must be at least ${MIN_SLUG_LENGTH} characters.`)
    .max(MAX_SLUG_LENGTH, `Identifier cannot exceed ${MAX_SLUG_LENGTH} characters.`)
    .regex(
      SLUG_PATTERN,
      "Use lowercase letters, numbers, and single hyphens between them.",
    ),
});

export type CreateOrganizationFormData = z.infer<typeof createOrganizationSchema>;
