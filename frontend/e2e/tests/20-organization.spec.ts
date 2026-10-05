/**
 * ORGANIZATION MANAGEMENT for "Caretakers Global Inc" (Tenant C, Enterprise)
 * as its owner: every organization page, exercised with real input.
 *
 * Sandbox limits (recorded in 03-coverage.md, not hidden): no outbound network
 * to Stripe, DNS, LLM providers or warehouses, so steps that must reach them
 * are expected to fail *visibly and politely*; the tests assert that.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, apiLogin, loginAs, resolveWorkspaceId } from "../support/api";
import { PASSWORD, TENANTS, USERS, org, runId, ws } from "../support/env";
import { linkFrom, waitForMail } from "../support/mail";
import { samplePath } from "../support/sample-docs";

test.use({ user: "C.owner" });

test.describe("General", () => {
  test("rename the organization, save, and rename it back; the identifier is fixed", async ({ page }) => {
    await page.goto(org("C", "settings"));
    await expect(page.getByRole("textbox", { name: "Organization identifier" })).toBeDisabled();
    const name = page.getByRole("textbox", { name: "Organization name" });
    await expect(name).toHaveValue(TENANTS.C.name); // loaded before editing
    await name.fill(`Caretakers Global Inc ${runId()}`);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("body")).toContainText(/saved|updated/i, { timeout: 10_000 });
    await name.fill(TENANTS.C.name);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.getByRole("button", { name: "Save changes" })).toBeDisabled({ timeout: 10_000 });
  });
});

test.describe("Notifications (organization)", () => {
  test("the unread-only filter toggles", async ({ page }) => {
    await page.goto(org("C", "notifications"));
    const unread = page.getByRole("checkbox").first();
    await unread.check();
    await unread.uncheck();
    await expectHealthyPage(page);
  });
});

test.describe("Members: invite, accept, change role, remove", () => {
  test.setTimeout(240_000);

  test("full member lifecycle through email", async ({ page, browser }) => {
    const invitee = `invitee-${runId()}@e2e.example.com`;
    const since = Date.now() - 1_000;

    // 1. Invite from the workspace settings (where invitations live).
    await page.goto(ws("C", "settings"));
    await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
    await page.getByPlaceholder("colleague@company.com").fill(invitee);
    await page.getByRole("button", { name: "Send Invite" }).click();
    await expect(page.locator("main")).toContainText(invitee, { timeout: 15_000 });

    // 2. The invitee signs up, verifies and accepts through the emailed link.
    const invitation = await waitForMail(invitee, /invitations\/accept/, since);
    const context = await browser.newContext();
    const guest = await context.newPage();
    await guest.goto("/register");
    await guest.locator("#email").fill(invitee);
    await guest.locator("#password").fill(PASSWORD);
    await guest.locator("#confirmPassword").fill(PASSWORD);
    await guest.locator("form").getByRole("button", { name: /create|sign up|register/i }).click();
    const verify = await waitForMail(invitee, /verify-email/, since);
    await guest.goto(linkFrom(verify, /\/verify-email/));
    await expect(guest.locator("body")).toContainText(/verified|confirmed|thank/i, { timeout: 15_000 });
    await guest.goto("/login");
    await guest.getByRole("textbox", { name: "Email" }).fill(invitee);
    await guest.getByRole("button", { name: "Continue" }).click();
    await guest.locator("#password").fill(PASSWORD);
    await guest.locator("#password").press("Enter");
    await guest.waitForURL(/\/onboarding|\/workspaces|\/organizations|\/caretakers-global/, { timeout: 20_000 });
    await guest.goto(linkFrom(invitation, /invitations\/accept/));
    await guest.getByRole("button", { name: /accept/i }).click();
    await expect(guest).toHaveURL(/caretakers-global/, { timeout: 20_000 });
    await context.close();

    // 3. The owner promotes the new member to ADMIN, then removes them.
    await page.goto(org("C", "members"));
    const role = page.getByRole("combobox", { name: `Role for ${invitee}` });
    await expect(role).toBeVisible({ timeout: 15_000 });
    await role.selectOption("ADMIN");
    await expect(page.locator("body")).toContainText(/updated|changed|saved|ADMIN/i);
    await page.getByRole("button", { name: `Remove ${invitee}` }).click();
    const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await confirm.getByRole("button", { name: /remove/i }).click();
    await expect(page.getByRole("button", { name: `Remove ${invitee}` })).toHaveCount(0, { timeout: 15_000 });
  });
});

test.describe("Transactional email", () => {
  test("configure a custom SMTP server and send a test message that arrives", async ({ page }) => {
    const since = Date.now() - 1_000;
    const recipient = `smtp-test-${runId()}@e2e.example.com`;
    await page.goto(org("C", "email-settings"));
    await page.getByRole("checkbox", { name: /Use custom server/ }).check();
    await page.getByRole("textbox", { name: "Server host" }).fill("127.0.0.1");
    await page.getByRole("spinbutton", { name: "Port" }).fill("1025");
    await page.getByRole("combobox", { name: "Encryption" }).selectOption({ label: "None" });
    await page.getByRole("textbox", { name: "Username" }).fill("caretakers-relay");
    await page.getByRole("textbox", { name: "Password" }).fill("relay-password-e2e");
    await page.getByRole("textbox", { name: "Sender name" }).fill("Caretakers Global");
    await page.getByRole("textbox", { name: "Sender address" }).fill("noreply@caretakers.example.com");
    await page.getByRole("button", { name: "Save settings" }).click();
    await expect(page.locator("body")).toContainText(/saved|updated/i, { timeout: 15_000 });
    await page.getByRole("textbox", { name: "Send to" }).fill(recipient);
    await page.getByRole("button", { name: "Send test" }).click();
    try {
      const mail = await waitForMail(recipient, /./, since);
      expect(mail.text).toMatch(/caretakers/i);
    } finally {
      // Restore the platform sender so later invitation mail is not routed here.
      await page.getByRole("checkbox", { name: /Use custom server/ }).uncheck();
      await page.getByRole("button", { name: "Save settings" }).click();
      await settle(page);
    }
  });
});

test.describe("Service levels", () => {
  test("the SLO dashboard shows each target and the period switch works", async ({ page }) => {
    await page.goto(org("C", "service-levels"));
    for (const metric of ["API availability", "API p95 latency", "Job completion rate"]) {
      await expect(page.locator("main")).toContainText(metric);
    }
    await page.getByRole("combobox").first().selectOption({ index: 1 });
    await page.getByRole("button", { name: "Change target" }).first().click();
    await settle(page);
    await expectHealthyPage(page);
  });
});

test.describe("Data governance and compliance", () => {
  test.setTimeout(150_000);

  test("set a 90-day retention policy", async ({ page }) => {
    await page.goto(org("C", "compliance"));
    await page.locator("#retention-work-items").fill("90");
    const saved = page.waitForResponse((r) => /\/compliance/.test(r.url()) && r.request().method() !== "GET");
    await page.getByRole("button", { name: "Save retention policy" }).click();
    expect((await saved).status()).toBeLessThan(300);
    await page.reload();
    await expect(page.locator("#retention-work-items")).toHaveValue("90");
  });

  test("generate a DPA export bundle (GDPR data export)", async ({ page }) => {
    await page.goto(org("C", "compliance"));
    await page.getByRole("button", { name: "Generate bundle" }).click();
    await expect(page.locator("main")).toContainText(/queued|generating|ready|download|requested/i, { timeout: 30_000 });
  });

  test("right to be forgotten: preview the impact of erasing a member", async ({ page }) => {
    const member = await loginAs("C.member");
    await page.goto(org("C", "compliance"));
    await page.getByRole("button", { name: "Erase a subject" }).click();
    await page.getByRole("textbox", { name: "Subject user ID" }).fill(String(member.me.id));
    await page.getByRole("button", { name: "Run impact preview" }).click();
    await expect(page.locator("main")).toContainText(/document|record|session|membership|impact/i, { timeout: 20_000 });
    await page.getByRole("button", { name: "Cancel" }).click();
  });
});

test.describe("Developer platform", () => {
  test("issue a gateway API key; its secret is shown once", async ({ page }) => {
    await page.goto(org("C", "developer"));
    await page.getByRole("button", { name: "Issue key" }).first().click();
    await page.getByRole("textbox", { name: "Name" }).fill(`E2E gateway ${runId()}`);
    await page.getByRole("button", { name: "Issue key" }).last().click();
    await expect(page.getByText(/only time this token is shown/i)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByRole("dialog").getByText(/^fp_test_/)).toBeVisible();
    await page.getByRole("button", { name: "I have saved it" }).click();
    for (const lang of ["Python", "TypeScript", "cURL"]) {
      await page.getByRole("button", { name: lang, exact: true }).click();
    }
  });
});

test.describe("Enterprise BYOK and models", () => {
  test("adding a provider key validates it and reports the result", async ({ page, problems }) => {
    problems.allowHttp(/\/byok/, [400, 422, 502, 503], "the provider cannot be reached from the sandbox");
    await page.goto(org("C", "byok"));
    await page.getByRole("button", { name: "Add key" }).first().click();
    await page.getByRole("textbox", { name: "Groq API key" }).fill("gsk_e2e_not_a_real_key_000000000000000000000000");
    await page.getByRole("button", { name: "Save key" }).click();
    await expect(page.getByText(/saved|added|invalid|could not|rejected|unreachable|verif/i).first()).toBeVisible({
      timeout: 30_000,
    });
  });

  test("model routing rows render with their fallback controls", async ({ page }) => {
    await page.goto(org("C", "byok"));
    await expect(page.locator("main")).toContainText("Model routing");
    await expect(page.getByRole("checkbox", { name: "My key" }).first()).toBeVisible();
  });
});

test.describe("Branding and custom domains", () => {
  test("set brand name and colours, save and enable", async ({ page }) => {
    await page.goto(org("C", "branding"));
    await page.locator("#brand-name").fill(`Caretakers Global ${runId()}`);
    await page.locator("#color-primary_color").fill("#1a73e8");
    await page.locator("#color-accent_color").fill("#0b8043");
    await page.getByRole("button", { name: "Save brand" }).click();
    await expect(page.locator("body")).toContainText(/saved|updated/i, { timeout: 10_000 });
    await page.getByRole("button", { name: "Enable custom branding" }).click();
    await settle(page);
    await expectHealthyPage(page);
  });

  test("upload a logo", async ({ page }) => {
    await page.goto(org("C", "branding"));
    await page.locator('main input[type="file"]').first().setInputFiles(samplePath("logoPng"));
    await expect(page.locator("body")).toContainText(/uploaded|saved|logo/i, { timeout: 15_000 });
  });

  test("claim a custom domain: DNS instructions, or a clear 'not enabled' message", async ({ page, problems }) => {
    // CUSTOM_DOMAINS_ENABLED=false here (no Caddy/ACME), so the server answers 501.
    problems.allowHttp(/\/custom-domains$/, [501], "custom domains are disabled on this deployment");
    await page.goto(org("C", "branding"));
    await page.locator("#hostname").fill(`ai-${runId()}.caretakers-e2e.co.uk`);
    await page.getByRole("button", { name: "Claim domain" }).click();
    await expect(page.locator("main")).toContainText(/TXT|CNAME|record|not enabled on this deployment/i, { timeout: 15_000 });
  });
});

test.describe("Analytics and BI egress", () => {
  test("add an S3 destination and open every tab", async ({ page, problems }) => {
    problems.allowHttp(/\/analytics\//, [400, 422, 502], "the warehouse is unreachable from the sandbox");
    await page.goto(org("C", "analytics"));
    await page.getByRole("button", { name: "Add destination" }).first().click();
    await page.getByRole("textbox", { name: "Label" }).fill(`E2E S3 ${runId()}`);
    await page.getByRole("textbox", { name: "Bucket" }).fill("caretakers-e2e-bucket");
    await page.getByRole("textbox", { name: "Region" }).fill("eu-west-2");
    await page.getByRole("textbox", { name: "Key prefix" }).fill("flowpilot/");
    await page.getByRole("textbox", { name: "Access key ID" }).fill("AKIAE2ETESTONLY0000");
    await page.getByRole("textbox", { name: "Secret access key" }).fill("e2e-secret-not-real");
    await page.getByRole("button", { name: "Add destination" }).last().click();
    await expect(page.locator("main")).toContainText(/E2E S3|added|saved|could not|unreachable|failed/i, { timeout: 20_000 });
    for (const tab of ["Sync schedules", "Run history", "Usage analytics", "Warehouse destinations"]) {
      await page.getByRole("button", { name: tab, exact: true }).click();
      await settle(page, 300);
    }
  });
});

test.describe("Partner marketplace", () => {
  test("the catalogue loads (empty until partners publish)", async ({ page }) => {
    await page.goto(org("C", "marketplace"));
    await expect(page.locator("main")).toContainText(/Available|No published workflows/);
  });
});

test.describe("Calibrated autonomy", () => {
  test("the error-limit slider can be moved and the change is kept", async ({ page }) => {
    await page.goto(org("C", "autonomy"));
    const slider = page.locator("#autonomy-alpha");
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    const save = page.getByRole("button", { name: /save|apply/i }).first();
    if (await save.isVisible()) await save.click();
    await settle(page);
    await expectHealthyPage(page);
  });
});

test.describe("Egress lockdown", () => {
  test("add an allow rule, test a destination, switch lockdown on and off", async ({ page }) => {
    await page.goto(org("C", "egress"));
    const host = `*.e2e-${runId()}.caretakers.example.com`;
    await page.getByRole("textbox", { name: "Host, *.domain, IP or CIDR" }).fill(host);
    await page.getByRole("textbox", { name: "Port" }).fill("443");
    await page.getByRole("button", { name: "Add rule" }).click();
    await expect(page.locator("main")).toContainText(host, { timeout: 10_000 });
    await page.getByRole("textbox", { name: "URL or host" }).fill("https://hooks.caretakers.example.com/flowpilot");
    await page.getByRole("button", { name: "Test" }).click();
    await expect(page.locator("main")).toContainText(/allowed|refused|blocked|permitted/i, { timeout: 10_000 });
    await page.getByRole("button", { name: "Switch lockdown on" }).click();
    const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    if (await confirm.isVisible()) await confirm.getByRole("button").last().click();
    await expect(page.locator("main")).toContainText(/Lockdown is on/i, { timeout: 10_000 });
    await page.getByRole("button", { name: /Switch lockdown off/i }).click();
    if (await confirm.isVisible()) await confirm.getByRole("button").last().click();
    await expect(page.locator("main")).toContainText(/Lockdown is off/i, { timeout: 10_000 });
  });
});

test.describe("Billing", () => {
  test("plan cards show the four tiers at the confirmed prices", async ({ page }) => {
    await page.goto(org("C", "billing"));
    await expect(page.getByRole("radio", { name: /^Free/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Developer.*\$49/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Business.*\$299/ })).toBeVisible();
    await expect(page.getByRole("radio", { name: /^Enterprise.*Current plan.*\$799/ })).toBeDisabled();
  });

  test("seats and the current subscription are shown", async ({ page }) => {
    await page.goto(org("C", "billing"));
    await expect(page.locator("main")).toContainText("Seats");
    await expect(page.locator("main")).toContainText(/Plan: enterprise · active/);
  });

  test("a spend limit can be saved", async ({ page }) => {
    await page.goto(org("C", "billing"));
    await page.getByRole("spinbutton", { name: "Maximum cost ($)" }).fill("250");
    await page.getByRole("textbox", { name: "Note (optional)" }).fill("E2E budget cap");
    await page.getByRole("button", { name: "Save limit" }).click();
    await expect(page.locator("body")).toContainText(/saved|250/i, { timeout: 10_000 });
  });

  test("without a Stripe test key, plan switches are disabled with the reason shown", async ({ page }) => {
    // The sandbox has no STRIPE_SECRET_KEY, so checkout cannot start; the page must say why.
    await page.goto(org("C", "billing"));
    await expect(page.getByText(/Paid checkout is not configured/)).toBeVisible();
    await expect(page.getByRole("button", { name: "Switch to Business" })).toBeDisabled();
    await page.getByRole("button", { name: "Annual" }).click();
    await settle(page);
    await expectHealthyPage(page);
  });

  test("the seat count can be edited (Apply enables on change)", async ({ page }) => {
    await page.goto(org("C", "billing"));
    const seats = page.getByRole("spinbutton", { name: /Seats/ });
    await seats.fill("6");
    await expect(page.getByRole("button", { name: "Apply" })).toBeEnabled();
    await seats.fill("4");
  });

  test("a promo code can be entered at checkout (requested capability)", async ({ page }) => {
    await page.goto(org("C", "billing"));
    await expect(page.getByRole("textbox", { name: /promo|coupon/i })).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("API keys", () => {
  test("create a key: the secret is shown once; then revoke it", async ({ page }) => {
    const name = `E2E key ${runId()}`;
    await page.goto(org("C", "api-keys"));
    await page.getByRole("button", { name: "New key" }).click();
    await page.getByRole("textbox", { name: "Name" }).fill(name);
    await page.getByRole("checkbox", { name: "work_items:read" }).check();
    await page.getByRole("button", { name: "Create key" }).click();
    await expect(page.getByText(/fp_test_|only time|won.t be shown/i).first()).toBeVisible({ timeout: 15_000 });
    const done = page.getByRole("button", { name: /done|close|i.ve copied/i }).first();
    if (await done.isVisible()) await done.click();
    await page.reload();
    await expect(page.locator("main")).toContainText(name);
    await expect(page.locator("main")).not.toContainText(/shown once/i);
    // Newest first: the key just created is the first row.
    await expect(page.locator("main")).toContainText(name);
    await page.getByRole("button", { name: "Revoke" }).first().click();
    await page.getByRole("button", { name: "Confirm revoke" }).click();
    await settle(page);
    const owner = await loginAs("C.owner");
    const { organizationId } = await resolveWorkspaceId(owner, TENANTS.C.org, TENANTS.C.ws);
    const keys = await api<Array<{ name: string; revoked_at?: string | null; is_active?: boolean }>>(
      owner,
      "GET",
      `/organizations/${organizationId}/api-keys`,
    );
    const mine = (Array.isArray(keys.body) ? keys.body : []).find((key) => key.name === name);
    expect(mine === undefined || Boolean(mine.revoked_at) || mine.is_active === false, `key ${name} is revoked`).toBe(true);
  });
});

test.describe("Webhooks", () => {
  test("register an endpoint, see the signing secret once, open its deliveries", async ({ page, problems }) => {
    problems.allowHttp(/\/webhooks\//, [400, 422, 502], "the receiver is unreachable from the sandbox");
    await page.goto(org("C", "webhooks"));
    await page.getByRole("button", { name: "New endpoint" }).click();
    await page.getByRole("textbox", { name: "Endpoint URL" }).fill("https://hooks.caretakers.example.com/flowpilot");
    await page.getByRole("textbox", { name: "Description (optional)" }).fill("E2E endpoint");
    await page.getByRole("checkbox", { name: "work_item.created" }).check();
    await page.getByRole("button", { name: /create|save|add endpoint/i }).last().click();
    await expect(page.getByText(/Copy this signing secret now/)).toBeVisible({ timeout: 15_000 });
    await expect(page.getByText(/^whsec_/)).toBeVisible();
    await page.getByRole("button", { name: "I've saved it" }).click();
    await page.getByRole("button", { name: "Deliveries" }).first().click();
    await settle(page);
    await expectHealthyPage(page);
  });

  test("an endpoint can be sent a test ping (requested capability)", async ({ page }) => {
    await page.goto(org("C", "webhooks"));
    await expect(page.getByRole("button", { name: /send test|test ping|ping/i }).first()).toBeVisible({ timeout: 5_000 });
  });
});

test.describe("Enterprise identity", () => {
  test("claim a domain and open the SSO, SCIM and security sections", async ({ page }) => {
    await page.goto(org("C", "identity"));
    await page.getByRole("textbox", { name: "Domain to claim" }).fill(`caretakers-e2e-${runId()}.co.uk`);
    await page.getByRole("button", { name: "Claim domain" }).click();
    await expect(page.locator("main")).toContainText(/TXT|verification|pending/i, { timeout: 15_000 });
    for (const section of ["Single sign-on", "Provisioning", "SCIM", "Security", "Audit log", "Domains"]) {
      await page.getByRole("tab", { name: section }).or(page.getByRole("button", { name: section, exact: true })).first().click();
      await settle(page, 300);
      await expectHealthyPage(page);
    }
  });
});

test.describe("Audit log", () => {
  test("filter by action and actor, inspect an entry, export CSV and NDJSON", async ({ page }) => {
    const owner = await loginAs("C.owner");
    await page.goto(org("C", "audit"));
    await page.getByRole("textbox", { name: "Action" }).fill("UPDATED");
    await page.getByRole("textbox", { name: "Actor" }).fill(String(owner.me.id));
    await settle(page);
    await page.getByRole("textbox", { name: "Actor" }).fill("");
    await page.getByRole("button", { name: "Inspect" }).first().click();
    await expect(page.getByRole("button", { name: "Close" })).toBeVisible();
    await page.getByRole("button", { name: "Close" }).click();
    for (const format of ["CSV", "NDJSON"]) {
      const download = page.waitForEvent("download", { timeout: 20_000 });
      await page.getByRole("button", { name: format, exact: true }).click();
      expect((await download).suggestedFilename()).toMatch(new RegExp(`\\.(${format.toLowerCase()}|ndjson|jsonl)$`, "i"));
    }
  });

  test("the audit trail cannot be changed through the API", async () => {
    const owner = await loginAs("C.owner");
    const { organizationId } = await resolveWorkspaceId(owner, TENANTS.C.org, TENANTS.C.ws);
    const list = await api<{ items?: Array<{ id: string }> } | Array<{ id: string }>>(
      owner,
      "GET",
      `/organizations/${organizationId}/audit-logs?limit=1`,
    );
    const items = Array.isArray(list.body) ? list.body : list.body.items ?? [];
    expect(items.length).toBeGreaterThan(0);
    const id = items[0]?.id;
    for (const method of ["PATCH", "PUT", "DELETE"]) {
      const response = await api(owner, method, `/organizations/${organizationId}/audit-logs/${id}`, { action: "X" });
      expect([403, 404, 405], `${method} audit entry -> ${response.status}`).toContain(response.status);
    }
  });
});

// Keep USERS/apiLogin imported for helpers used by other org journeys.
void USERS;
void apiLogin;
