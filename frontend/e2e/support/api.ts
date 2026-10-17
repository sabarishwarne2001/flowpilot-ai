/**
 * A small API client that runs in Node, not in the browser. Used to sign in
 * (each test gets its own fresh session: refresh tokens rotate and a reused
 * one ends the session family, so saved sessions cannot be shared), to upload
 * the sample documents, and to prove the SERVER refuses what the UI hides
 * (402 for a missing plan capability, 403 for a missing role).
 */
import fs from "node:fs";
import path from "node:path";

import { API_BASE, PASSWORD, USERS, type UserKey } from "./env";

export interface ApiSession {
  readonly userKey: UserKey | null;
  readonly email: string;
  readonly accessToken: string;
  readonly refreshToken: string;
  readonly me: Record<string, unknown>;
}

export interface ApiResult<T = unknown> {
  readonly status: number;
  readonly body: T;
  readonly text: string;
}

const REFRESH_COOKIE = "servabo_refresh";

function parseRefreshCookie(response: Response): string {
  const cookies = response.headers.getSetCookie?.() ?? [];
  for (const cookie of cookies) {
    const [pair] = cookie.split(";");
    const [name, ...rest] = (pair ?? "").split("=");
    if (name?.trim() === REFRESH_COOKIE) {
      return rest.join("=").trim();
    }
  }
  throw new Error(`login response carried no ${REFRESH_COOKIE} cookie`);
}

/**
 * fetch, retried once when the server had already closed the pooled keep-alive connection
 * ("other side closed"): uvicorn drops an idle connection after a few seconds, and Node can pick
 * it up in the same instant. The request never reached the server, so sending it again is safe.
 * While a body is being written (an upload) the same race shows as EPIPE or ECONNRESET (CI, spec
 * 71 and spec 78): the server closed the socket before reading the request, so it never ran.
 */
export async function fetchOnce(url: string, init: RequestInit): Promise<Response> {
  try {
    return await fetch(url, init);
  } catch (error) {
    const cause = (error as { cause?: { code?: string; message?: string } }).cause;
    if (cause?.code === "UND_ERR_SOCKET" || cause?.code === "EPIPE" || cause?.code === "ECONNRESET"
      || /other side closed/.test(String(cause?.message ?? ""))) {
      return fetch(url, init);
    }
    throw error;
  }
}

export async function apiLogin(email: string, password = PASSWORD): Promise<ApiSession> {
  const response = await fetchOnce(`${API_BASE}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body: new URLSearchParams({ username: email, password }).toString(),
  });
  const text = await response.text();
  if (response.status !== 200) {
    throw new Error(`login as ${email} failed: HTTP ${response.status} ${text.slice(0, 300)}`);
  }
  const { access_token: accessToken } = JSON.parse(text) as { access_token: string };
  const refreshToken = parseRefreshCookie(response);
  const meResponse = await fetchOnce(`${API_BASE}/auth/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  const me = (await meResponse.json()) as Record<string, unknown>;
  const userKey =
    (Object.entries(USERS).find(([, user]) => user.email === email)?.[0] as UserKey | undefined) ?? null;
  return { userKey, email, accessToken, refreshToken, me };
}

export async function loginAs(userKey: UserKey): Promise<ApiSession> {
  return apiLogin(USERS[userKey].email);
}

/**
 * Campaign session 4 (browser-suite isolation). The seeded people are shared by tests running in
 * parallel: ending every session of one ("sign out everywhere") signs it out of all of them. A test
 * that needs it signs in a person of its own (`newCustomer` in support/journey.ts).
 */
export const SHARED_PERSONA_SIGN_OUT =
  "A test tried to end every session of a seeded person (POST /auth/logout-all). Tests running in parallel " +
  "share that person and would all be signed out. End only this test's own session (DELETE /auth/sessions/{id}), " +
  "or sign in a person of the test's own (newCustomer in support/journey.ts).";

export function isSharedPersona(email: string | null | undefined): boolean {
  return Boolean(email) && Object.values(USERS).some((user) => user.email === email);
}

export async function api<T = unknown>(
  session: ApiSession | null,
  method: string,
  pathname: string,
  body?: unknown,
  extraHeaders: Record<string, string> = {},
): Promise<ApiResult<T>> {
  if (/^\/auth\/logout-all\b/.test(pathname) && isSharedPersona(session?.email)) {
    throw new Error(SHARED_PERSONA_SIGN_OUT);
  }
  const headers: Record<string, string> = { Accept: "application/json", ...extraHeaders };
  if (session) {
    headers.Authorization = `Bearer ${session.accessToken}`;
  }
  let payload: BodyInit | undefined;
  if (body instanceof FormData) {
    payload = body;
  } else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const url = pathname.startsWith("http") ? pathname : `${API_BASE}${pathname}`;
  const response = await fetchOnce(url, { method, headers, body: payload ?? null });
  const text = await response.text();
  let parsed: unknown = text;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }
  return { status: response.status, body: parsed as T, text };
}

interface MeContext {
  organizations: Array<{
    organization_id: string;
    organization_slug: string;
    role: string;
    workspaces: Array<{ id: string; slug: string; effective_role: string }>;
  }>;
}

/** The organization and workspace ids for a slug pair, from GET /me/context. */
export async function resolveWorkspaceId(
  session: ApiSession,
  orgSlug: string,
  workspaceSlug: string,
): Promise<{ organizationId: string; workspaceId: string }> {
  const context = await api<MeContext>(session, "GET", "/me/context");
  const organization = context.body.organizations?.find((o) => o.organization_slug === orgSlug);
  if (!organization) {
    throw new Error(`organization ${orgSlug} not visible to ${session.email}: ${context.text.slice(0, 300)}`);
  }
  const workspace = organization.workspaces.find((w) => w.slug === workspaceSlug);
  if (!workspace) {
    throw new Error(`workspace ${orgSlug}/${workspaceSlug} not visible to ${session.email}`);
  }
  return { organizationId: organization.organization_id, workspaceId: workspace.id };
}

export async function uploadFile(
  session: ApiSession,
  workspaceId: string,
  filePath: string,
  mime = filePath.endsWith(".png") ? "image/png" : "application/pdf",
): Promise<ApiResult<Record<string, unknown>>> {
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(filePath)], { type: mime }), path.basename(filePath));
  return api<Record<string, unknown>>(session, "POST", `/workspaces/${workspaceId}/work-items`, form);
}

export async function listWorkItems(
  session: ApiSession,
  workspaceId: string,
  // The API pages with `limit` (at most 100); `pageSize` is not one of its parameters.
  query = "limit=100",
): Promise<Array<Record<string, unknown>>> {
  const result = await api<unknown>(session, "GET", `/workspaces/${workspaceId}/work-items?${query}`);
  const body = result.body as { items?: unknown[]; data?: unknown[] } | unknown[];
  if (Array.isArray(body)) {
    return body as Array<Record<string, unknown>>;
  }
  return ((body?.items ?? body?.data ?? []) as Array<Record<string, unknown>>);
}
