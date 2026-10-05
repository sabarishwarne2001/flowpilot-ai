/**
 * AUTH and public routes, end to end through the real forms and real email
 * (delivered to the local SMTP sink): sign-up, email verification, first
 * organization, login, wrong password, logout, password reset, session expiry.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, apiLogin, loginAs } from "../support/api";
import { PASSWORD, USERS, runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";

const NEW_PASSWORD = "E2e-Reset-Pass-2026!";

async function signUp(page: import("@playwright/test").Page, email: string): Promise<void> {
  await page.goto("/register");
  await page.locator("#email").fill(email);
  await page.locator("#password").fill(PASSWORD);
  await page.locator("#confirmPassword").fill(PASSWORD);
  await page.locator("form").getByRole("button", { name: /create|sign up|register/i }).click();
}

async function loginThroughForm(page: import("@playwright/test").Page, email: string, password: string): Promise<void> {
  // Two steps: the email first (single sign-on discovery), then the password.
  await page.goto("/login");
  await page.getByRole("textbox", { name: "Email" }).fill(email);
  await page.getByRole("button", { name: "Continue" }).click();
  await page.locator("#password").fill(password);
  await page.locator("#password").press("Enter");
}

test.describe("sign-up, verification and first organization", () => {
  test.setTimeout(150_000);

  test("a new user signs up, verifies by email, signs in and creates an organization", async ({ page }) => {
    const email = `signup-${runId()}@e2e.example.com`;
    const since = Date.now() - 1_000;
    await signUp(page, email);
    await expect(page.locator("body")).toContainText(/check your (email|inbox)|verification|sent/i);

    const mail = await waitForMail(email, /verify-email/, since);
    await page.goto(linkFrom(mail, /\/verify-email/));
    await expect(page.locator("body")).toContainText(/verified|confirmed|thank/i, { timeout: 15_000 });

    await loginThroughForm(page, email, PASSWORD);
    await expect(page).toHaveURL(/\/onboarding|\/organizations\/new|\/workspaces/, { timeout: 20_000 });
    await expectHealthyPage(page);

    // First organization (onboarding).
    const orgName = `E2E Signup Org ${runId()}`;
    await page.getByRole("textbox").first().fill(orgName);
    await page.getByRole("button", { name: /create|continue|get started/i }).first().click();
    await expect(page.locator("body")).toContainText(orgName, { timeout: 20_000 });
    await expectHealthyPage(page);
  });

  test("signing up twice with the same email does not reveal that the account exists", async ({ page }) => {
    await signUp(page, USERS["C.owner"].email);
    await expect(page.locator("body")).toContainText(/check your (email|inbox)|verification|sent/i);
    await expect(page.locator("body")).not.toContainText(/already (exists|registered|taken)/i);
  });
});

test.describe("login and logout", () => {
  test("the seeded Enterprise owner signs in through the form and lands in a workspace", async ({ page }) => {
    await loginThroughForm(page, USERS["C.owner"].email, PASSWORD);
    await expect(page).toHaveURL(/\/caretakers-global\/(operations|finance)|\/workspaces/, { timeout: 20_000 });
    await expectHealthyPage(page);
  });

  test("a wrong password shows a generic error and stays on the login page", async ({ page, problems }) => {
    problems.allowHttp(/\/auth\/login$/, [400, 401], "wrong password is refused");
    await loginThroughForm(page, USERS["C.admin"].email, "Not-The-Password-1!");
    await expect(page.locator("body")).toContainText(/incorrect|invalid|wrong|could not sign/i);
    await expect(page).toHaveURL(/\/login/);
  });

  test("sign out ends the session; the app is not reachable afterwards", async ({ page, problems }) => {
    problems.allowHttp(/\/auth\/refresh$/, [401], "no session after sign-out");
    await loginThroughForm(page, USERS["C.admin"].email, PASSWORD);
    await expect(page).toHaveURL(/\/caretakers-global\//, { timeout: 20_000 });
    await page.getByRole("button", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await page.goto(ws("C", "work-items"));
    await expect(page).toHaveURL(/\/login/);
  });
});

test.describe("password reset", () => {
  test.setTimeout(150_000);

  test("forgot password sends a link; the new password works and the old one does not", async ({ page, problems }) => {
    problems.allowHttp(/\/auth\/login$/, [400, 401], "the old password is refused after the reset");
    // A throwaway account so the seeded users keep their password.
    const email = `reset-${runId()}@e2e.example.com`;
    const since = Date.now() - 1_000;
    await signUp(page, email);
    await page.goto(linkFrom(await waitForMail(email, /verify-email/, since), /\/verify-email/));
    await expect(page.locator("body")).toContainText(/verified|confirmed|thank/i, { timeout: 15_000 });

    const resetSince = Date.now() - 1_000;
    await page.goto("/forgot-password");
    await page.getByPlaceholder("you@example.com").fill(email);
    await page.locator("form").getByRole("button").first().click();
    await expect(page.locator("body")).toContainText(/check your (email|inbox)|if an account|sent/i);

    const mail = await waitForMail(email, /reset-password/, resetSince);
    await page.goto(linkFrom(mail, /\/reset-password/));
    await page.getByPlaceholder("New password", { exact: true }).fill(NEW_PASSWORD);
    await page.getByPlaceholder("Confirm new password").fill(NEW_PASSWORD);
    await page.locator("form").getByRole("button").first().click();
    await expect(page.locator("body")).toContainText(/password (has been )?(reset|changed|updated)|sign in/i, { timeout: 15_000 });

    await expect(apiLogin(email, PASSWORD)).rejects.toThrow(/HTTP 40[01]/);
    const fresh = await apiLogin(email, NEW_PASSWORD);
    expect(fresh.accessToken.length).toBeGreaterThan(20);
  });

  test("forgot password for an unknown email gives the same answer (no account enumeration)", async ({ page }) => {
    await page.goto("/forgot-password");
    await page.getByPlaceholder("you@example.com").fill(`nobody-${runId()}@e2e.example.com`);
    await page.locator("form").getByRole("button").first().click();
    await expect(page.locator("body")).toContainText(/check your (email|inbox)|if an account|sent/i);
  });
});

test.describe("session expiry", () => {
  test.use({ user: "C.admin" });

  test("when the session is ended elsewhere, the next page load returns to sign-in", async ({ page, session, problems }) => {
    problems.allowHttp(/\/auth\/refresh$|\/api\/v1\//, [401], "the session was revoked on the server");
    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    // "Sign out everywhere" from another device.
    const other = await loginAs("C.admin");
    const revoke = await api(other, "POST", "/auth/logout-all");
    expect([200, 204]).toContain(revoke.status);
    expect(session).not.toBeNull();
    await page.reload();
    await settle(page);
    await expect(page).toHaveURL(/\/login/, { timeout: 20_000 });
  });
});
