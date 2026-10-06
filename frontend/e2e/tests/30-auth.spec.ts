/**
 * AUTH and public routes, end to end through the real forms and real email
 * (delivered to the local SMTP sink): sign-up, email verification, first
 * organization, login, wrong password, logout, password reset, session expiry.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, apiLogin, loginAs } from "../support/api";
import { PASSWORD, USERS, runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { totpCode } from "../support/totp";

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

test.describe("two-factor sign-in (N-017)", () => {
  test.setTimeout(180_000);

  test("turn it on, sign in with an app code, then with a recovery code, then turn it off", async ({ page, problems }) => {
    problems.allowHttp(/\/auth\/login\/mfa$/, [401], "a wrong code is refused");
    problems.allowHttp(/\/auth\/refresh$/, [401], "no session after sign-out");
    const email = `mfa-${runId()}@e2e.example.com`;
    const since = Date.now() - 1_000;
    await signUp(page, email);
    const mail = await waitForMail(email, /verify-email/, since);
    await page.goto(linkFrom(mail, /\/verify-email/));
    await expect(page.locator("body")).toContainText(/verified|confirmed|thank/i, { timeout: 15_000 });
    await loginThroughForm(page, email, PASSWORD);
    await expect(page).toHaveURL(/\/onboarding|\/organizations\/new|\/workspaces/, { timeout: 20_000 });
    await page.getByRole("textbox").first().fill(`E2E MFA Org ${runId()}`);
    await page.getByRole("button", { name: /create|continue|get started/i }).first().click();
    await expect(page).not.toHaveURL(/\/onboarding|\/organizations\/new|\/workspaces/, { timeout: 20_000 });
    const workspaceBase = new URL(page.url()).pathname.split("/").slice(0, 3).join("/");

    // Turn it on: password, scan (here: the typed-in key), a code from the app.
    await page.goto(`${workspaceBase}/settings`);
    const panel = page.getByRole("region", { name: "Two-factor sign-in" });
    await expect(panel.getByTestId("mfa-state")).toContainText("Off.", { timeout: 15_000 });
    await panel.getByRole("button", { name: "Turn on two-factor sign-in" }).click();
    await panel.getByLabel("Current password").fill(PASSWORD);
    await panel.getByRole("button", { name: "Continue" }).click();
    await expect(panel.getByRole("img", { name: "QR code to scan with your authenticator app" })).toBeVisible();
    const secret = (await panel.getByTestId("mfa-secret").innerText()).replace(/\s/g, "");
    await panel.getByLabel("6-digit code from the app").fill(totpCode(secret));
    await panel.getByRole("button", { name: "Turn on", exact: true }).click();
    const codes = panel.getByRole("list", { name: "Recovery codes" }).getByRole("listitem");
    await expect(codes).toHaveCount(10);
    const recovery = (await codes.nth(0).innerText()).trim();
    const secondRecovery = (await codes.nth(1).innerText()).trim();
    await panel.getByRole("button", { name: "I have saved them" }).click();
    await expect(panel.getByTestId("mfa-state")).toContainText("On. 10 recovery codes left.");
    await expectHealthyPage(page);

    // The password alone no longer signs in: a code is asked for, a wrong one is refused.
    await page.getByRole("button", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await loginThroughForm(page, email, PASSWORD);
    const codeField = page.getByRole("textbox", { name: "Authentication code" });
    await expect(codeField).toBeVisible({ timeout: 15_000 });
    await codeField.fill("000000");
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page.getByRole("alert")).toContainText(/code is not right/i);
    await expect(page).toHaveURL(/\/login/);
    // The step the app showed at turn-on is spent; the next one is the app's next code.
    await codeField.fill(totpCode(secret, Date.now() + 30_000));
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 });

    // A recovery code works once.
    await page.getByRole("button", { name: "Sign Out" }).click();
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
    await loginThroughForm(page, email, PASSWORD);
    await page.getByRole("button", { name: "Use a recovery code" }).click();
    await page.getByRole("textbox", { name: "Recovery code" }).fill(recovery);
    await page.getByRole("button", { name: "Verify" }).click();
    await expect(page).not.toHaveURL(/\/login/, { timeout: 20_000 });

    // Turning it off needs the password and a code.
    await page.goto(`${workspaceBase}/settings`);
    await expect(panel.getByTestId("mfa-state")).toContainText("On. 9 recovery codes left.", { timeout: 15_000 });
    await panel.getByRole("button", { name: "Turn off", exact: true }).click();
    await panel.getByLabel("Current password").fill(PASSWORD);
    // An app code here could fall in a 30-second step already used to sign in; a recovery code cannot.
    await panel.getByLabel("Code from the app, or a recovery code").fill(secondRecovery);
    await panel.getByRole("button", { name: "Turn off two-factor sign-in" }).click();
    await expect(panel.getByTestId("mfa-state")).toContainText("Off.", { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});

test.describe("login and logout", () => {
  test("the seeded Enterprise owner signs in through the form and lands in a workspace", async ({ page }) => {
    await loginThroughForm(page, USERS["C.owner"].email, PASSWORD);
    await expect(page).toHaveURL(/\/caretakers-global\/(operations|finance)|\/workspaces/, { timeout: 20_000 });
    await expect(page.locator("body")).toContainText(/Recent Activity|Choose a workspace/, { timeout: 20_000 });
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
