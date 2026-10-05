/**
 * Who the tests sign in as. Mirrors backend/scripts/seed_e2e.py, which is the
 * source of truth; global-setup.ts checks the two agree.
 */
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));

export const MODE = process.env.E2E_MODE === "dev" ? "dev" : "preview";
export const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3000);
export const WEB_ORIGIN = `http://localhost:${WEB_PORT}`;
/** Where Node (not the browser) reaches the API. */
export const API_ORIGIN = process.env.E2E_API_ORIGIN ?? "http://127.0.0.1:8000";
export const API_BASE = `${API_ORIGIN}/api/v1`;
/** Where the browser reaches the API: same origin under preview, :8000 under dev. */
export const BROWSER_API_ORIGIN = MODE === "dev" ? API_ORIGIN : WEB_ORIGIN;

export const E2E_DIR = path.resolve(HERE, "..");
export const REPO_ROOT = path.resolve(E2E_DIR, "..", "..");
export const BACKEND_DIR = path.join(REPO_ROOT, "backend");
export const STATE_FILE = path.join(E2E_DIR, ".state", "seed.json");
export const MAIL_DIR = process.env.E2E_MAIL_DIR ?? path.join(E2E_DIR, ".mail");

export const PASSWORD = "E2e-FlowPilot-Pass-2026!";

export type UserKey =
  | "A.owner"
  | "A.admin"
  | "A.billing"
  | "A.member"
  | "A.viewer"
  | "B.owner"
  | "C.owner"
  | "C.admin"
  | "C.member"
  | "C.viewer"
  | "P.superadmin";

export const USERS: Record<UserKey, { email: string; tenant: TenantKey }> = {
  "A.owner": { email: "a-owner@e2e.example.com", tenant: "A" },
  "A.admin": { email: "a-admin@e2e.example.com", tenant: "A" },
  "A.billing": { email: "a-billing@e2e.example.com", tenant: "A" },
  "A.member": { email: "a-member@e2e.example.com", tenant: "A" },
  "A.viewer": { email: "a-viewer@e2e.example.com", tenant: "A" },
  "B.owner": { email: "b-owner@e2e.example.com", tenant: "B" },
  "C.owner": { email: "c-owner@e2e.example.com", tenant: "C" },
  "C.admin": { email: "c-admin@e2e.example.com", tenant: "C" },
  "C.member": { email: "c-member@e2e.example.com", tenant: "C" },
  "C.viewer": { email: "c-viewer@e2e.example.com", tenant: "C" },
  "P.superadmin": { email: "p-superadmin@e2e.example.com", tenant: "P" },
};

export type TenantKey = "A" | "B" | "C" | "P";

export const TENANTS: Record<TenantKey, { org: string; ws: string; name: string; plan: string }> = {
  A: { org: "e2e-devco", ws: "main", name: "DevCo Labs", plan: "developer" },
  B: { org: "e2e-bizco", ws: "main", name: "BizCo Holdings", plan: "business" },
  C: { org: "caretakers-global", ws: "operations", name: "Caretakers Global Inc", plan: "enterprise" },
  P: { org: "e2e-platform-ops", ws: "ops", name: "FlowPilot Platform Ops", plan: "free" },
};

/** Second Enterprise workspace, for the workspace switcher. */
export const C_SECOND_WS = "finance";

export const ws = (tenant: TenantKey, sub = ""): string =>
  `/${TENANTS[tenant].org}/${TENANTS[tenant].ws}${sub ? `/${sub}` : ""}`;

export const org = (tenant: TenantKey, sub = ""): string =>
  `/organizations/${TENANTS[tenant].org}${sub ? `/${sub}` : ""}`;

/** A unique, sortable token for names that must not collide across runs. */
export const runId = (): string =>
  `${Date.now().toString(36)}${Math.floor(Math.random() * 1e4).toString(36)}`;
