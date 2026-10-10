/**
 * People who are not in the seed: sign a new address up and verify it through the
 * real mail, and open a browser context already signed in as an API session.
 * Shared by the two-party tests (ownership transfer, invitations).
 */
import { expect, type Browser, type BrowserContext, type Page } from "@playwright/test";

import { apiLogin, type ApiSession } from "./api";
import { API_BASE, BROWSER_API_ORIGIN, PASSWORD } from "./env";
import { linkFrom, waitForMail } from "./mail";

export function tokenOf(link: string): string | null {
  const url = new URL(link, "http://x");
  return new URLSearchParams(url.hash.replace(/^#/, "")).get("token") ?? url.searchParams.get("token");
}

export async function signUpVerified(email: string): Promise<ApiSession> {
  const since = Date.now() - 1_000;
  const registered = await fetch(`${API_BASE}/auth/register`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password: PASSWORD }),
  });
  expect(registered.status, await registered.text()).toBeLessThan(300);
  const mail = await waitForMail(email, /verify-email/, since);
  const token = tokenOf(linkFrom(mail, /\/verify-email/));
  const verified = await fetch(`${API_BASE}/auth/verify-email`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ token }),
  });
  expect(verified.status, await verified.text()).toBe(200);
  return apiLogin(email);
}

export async function openAs(browser: Browser, session: ApiSession): Promise<{ context: BrowserContext; page: Page }> {
  const context = await browser.newContext();
  await context.addCookies([
    {
      name: "flowpilot_refresh",
      value: session.refreshToken,
      domain: new URL(BROWSER_API_ORIGIN).hostname,
      path: "/api/v1/auth",
      httpOnly: true,
      secure: false,
      sameSite: "Lax",
    },
  ]);
  const persisted = JSON.stringify({ state: { user: session.me, isAuthenticated: true }, version: 0 });
  await context.addInitScript(
    ({ value }) => {
      if (!window.sessionStorage.getItem("__e2e_auth_seeded")) {
        window.localStorage.setItem("flowpilot_auth_session", value);
        window.sessionStorage.setItem("__e2e_auth_seeded", "1");
      }
    },
    { value: persisted },
  );
  return { context, page: await context.newPage() };
}
