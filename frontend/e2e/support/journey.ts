/**
 * Support for the end-to-end customer journeys (campaign session 4, §D).
 *
 * A journey starts from nothing: a person signs up, verifies by email and creates an organization,
 * then lives through one part of a customer's life in the real web app, against the real API and
 * worker. Two things a sandbox browser cannot do are played by `backend/scripts/e2e_stand_in.py`:
 * the clock (a trial reaching its end) and the payment gateway's side (paying at checkout, a
 * declined renewal, the card fixed, a cancellation), through FlowPilot's own reconcile code.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

import { expect, type Page } from "@playwright/test";

import { signUpVerified, tokenOf } from "./accounts";
import { api, apiLogin, resolveWorkspaceId, type ApiSession } from "./api";
import { BACKEND_DIR, BROWSER_API_ORIGIN, runId } from "./env";
import { linkFrom, waitForMail } from "./mail";
import { buildPdf } from "./sample-docs";

function python(): string {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const candidates = [
    path.join(BACKEND_DIR, ".venv", "bin", "python"),
    path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? "python3";
}

/** Run one stand-in command (see the script's docstring) and return what it applied. */
export function standIn(command: string, orgSlug: string | null, ...args: string[]): Record<string, unknown> {
  const argv = ["scripts/e2e_stand_in.py", command, ...(orgSlug ? ["--org", orgSlug] : []), ...args];
  const output = execFileSync(python(), argv, { cwd: BACKEND_DIR, encoding: "utf8", timeout: 120_000 });
  const lines = output.trim().split("\n");
  return JSON.parse(lines[lines.length - 1] ?? "{}") as Record<string, unknown>;
}

/** Queue a background job for the running worker (what its schedule would do later). */
export function runJob(jobType: string): void {
  standIn("run-job", null, "--job", jobType);
}

export interface Customer {
  email: string;
  session: ApiSession;
  orgSlug: string;
  orgId: string;
  wsSlug: string;
  wsId: string;
  /** In-app paths. */
  ws: (sub?: string) => string;
  org: (sub?: string) => string;
}

/** A new person who signed up, verified their email and created an organization (the trial starts). */
export async function newCustomer(label: string, organizationName?: string): Promise<Customer> {
  const id = runId();
  const email = `${label}-${id}@e2e.example.com`;
  const first = await signUpVerified(email);
  const orgSlug = `${label}-${id}`.toLowerCase().replace(/[^a-z0-9-]/g, "-").slice(0, 40);
  const created = await api(first, "POST", "/organizations", {
    organization_name: organizationName ?? `${label} ${id}`,
    organization_slug: orgSlug,
    workspace_name: "Main",
  });
  expect(created.status, created.text).toBe(201);
  const session = await apiLogin(email);
  const context = await api<{ organizations: Array<{ organization_slug: string; workspaces: Array<{ slug: string }> }> }>(
    session, "GET", "/me/context",
  );
  const wsSlug = context.body.organizations.find((o) => o.organization_slug === orgSlug)?.workspaces[0]?.slug ?? "main";
  const { organizationId, workspaceId } = await resolveWorkspaceId(session, orgSlug, wsSlug);
  return {
    email,
    session,
    orgSlug,
    orgId: organizationId,
    wsSlug,
    wsId: workspaceId,
    ws: (sub = "") => `/${orgSlug}/${wsSlug}${sub ? `/${sub}` : ""}`,
    org: (sub = "") => `/organizations/${orgSlug}${sub ? `/${sub}` : ""}`,
  };
}

/** Sign the page in as `email` (a fresh session), the way the strict fixture signs in seeded people. */
export async function signInAs(page: Page, email: string): Promise<ApiSession> {
  const session = await apiLogin(email);
  await page.context().clearCookies();
  await page.context().addCookies([
    {
      name: "servabo_refresh",
      value: session.refreshToken,
      domain: new URL(BROWSER_API_ORIGIN).hostname,
      path: "/api/v1/auth",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    },
  ]);
  const persisted = JSON.stringify({ state: { user: session.me, isAuthenticated: true }, version: 0 });
  await page.context().addInitScript(
    ({ value, marker }) => {
      if (window.sessionStorage.getItem("__e2e_auth_seeded") !== marker) {
        window.localStorage.setItem("servabo_auth_session", value);
        window.sessionStorage.setItem("__e2e_auth_seeded", marker);
      }
    },
    { value: persisted, marker: session.email },
  );
  return session;
}

/** A one-page invoice PDF on disk, unique to this run (in a plain folder: a test title's characters
 *  in the path made Chromium's file chooser drop the file without a word). */
export function invoicePdf(label: string): string {
  const dir = path.join(os.tmpdir(), "flowpilot-e2e-journeys");
  fs.mkdirSync(dir, { recursive: true });
  const file = path.join(dir, `${label}-${runId()}.pdf`);
  fs.writeFileSync(file, buildPdf([[`TAX INVOICE ${label}`, `Invoice Number: INV-${runId()}`,
    "Vendor: Acme Industrial Supplies Ltd", "Total Amount Due: 99.00 USD"]]));
  return file;
}

/** Upload from the workspace overview's tray, as a person would. */
export async function uploadInBrowser(page: Page, customer: Customer, file: string): Promise<void> {
  await page.goto(customer.ws());
  const dropzone = page.getByRole("button", { name: "Upload documents" });
  await expect(dropzone).toBeVisible({ timeout: 15_000 });
  const chooser = page.waitForEvent("filechooser");
  await dropzone.click();
  await (await chooser).setFiles(file);
  await expect(page.getByTestId("upload-queue-item")).toHaveCount(1);
  await page.getByRole("button", { name: "Start Ingestion" }).click();
  await expect(page.getByTestId("upload-queue-item")).toHaveCount(0, { timeout: 30_000 });
}

/** The workspace's documents, read through the API as the customer. */
export async function documents(customer: Customer): Promise<Array<Record<string, unknown>>> {
  const listed = await api<{ items?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>>(
    customer.session, "GET", `/workspaces/${customer.wsId}/work-items?page_size=100`,
  );
  const body = listed.body;
  return Array.isArray(body) ? body : (body.items ?? []);
}

/** Upload a document through the API as the customer (fast set-up for journeys that need many). */
export async function uploadViaApi(customer: Customer, label: string): Promise<string> {
  const file = invoicePdf(label);
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
  const response = await api<{ id: string }>(customer.session, "POST", `/workspaces/${customer.wsId}/work-items`, form);
  expect(response.status, response.text).toBe(201);
  return response.body.id;
}

/** Invite a new person into the customer's organization and have them accept by email. */
export async function addMember(
  customer: Customer,
  label: string,
  organizationRole = "MEMBER",
  workspaceRole = "CONTRIBUTOR",
): Promise<{ email: string; session: ApiSession }> {
  const email = `${label}-${runId()}@e2e.example.com`;
  const person = await signUpVerified(email);
  const since = Date.now() - 1_000;
  const invited = await api(customer.session, "POST", `/organizations/${customer.orgId}/invitations`, {
    email,
    organization_role: organizationRole,
    grants: [{ workspace_id: customer.wsId, role: workspaceRole }],
  });
  expect(invited.status, invited.text).toBe(201);
  const mail = await waitForMail(email, /invitations\/accept/, since);
  const accepted = await api(person, "POST", "/invitations/accept", { token: tokenOf(linkFrom(mail, /invitations\/accept/)) });
  expect(accepted.status, accepted.text).toBe(200);
  return { email, session: await apiLogin(email) };
}
