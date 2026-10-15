/**
 * Helm's mode memory (brief §10.1): the last mode per browser, and the last page in each mode per
 * organization, so the switch returns a person to where they were. localStorage can be missing or
 * refuse (a private window, blocked site data); every read and write is in try/catch and the app
 * behaves the same without it.
 */
export type HelmMode = "documents" | "helm";

const MODE_KEY = "helm.mode";
const pathKey = (organizationId: string, mode: HelmMode): string => `helm.path.${organizationId}.${mode}`;

/** Roles that see Helm: organization owners, admins and the billing role (brief §5.4). */
export const HELM_ROLES: ReadonlySet<string> = new Set(["OWNER", "ADMIN", "BILLING"]);

export const seesHelm = (role: string | null | undefined): boolean => HELM_ROLES.has(String(role ?? "").toUpperCase());

export const readMode = (): HelmMode | null => {
  try {
    const value = window.localStorage.getItem(MODE_KEY);
    return value === "helm" || value === "documents" ? value : null;
  } catch {
    return null;
  }
};

export const writeMode = (mode: HelmMode): void => {
  try {
    window.localStorage.setItem(MODE_KEY, mode);
  } catch {
    // The memory is a convenience; without it the switch still works.
  }
};

export const readLastPath = (organizationId: string, mode: HelmMode): string | null => {
  try {
    const value = window.localStorage.getItem(pathKey(organizationId, mode));
    return value && value.startsWith("/") && !value.startsWith("//") ? value : null;
  } catch {
    return null;
  }
};

export const writeLastPath = (organizationId: string, mode: HelmMode, path: string): void => {
  try {
    window.localStorage.setItem(pathKey(organizationId, mode), path);
  } catch {
    // As above.
  }
};
