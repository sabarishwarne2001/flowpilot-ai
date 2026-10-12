/**
 * Campaign session 3 (settings truthfulness) — what a settings page says it does, it does.
 *
 * Each test here pins a sentence on a settings page to the behaviour behind it, after the page
 * said more than the product did.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, resolveWorkspaceId } from "../support/api";
import { TENANTS, org, ws } from "../support/env";

test.describe("Organization mail server", () => {
  test.use({ user: "C.owner" });

  // F-305: the page said it delivers "password resets and security notices". Those always come
  // from the platform's mail service (so an account can be recovered when a tenant's server is
  // misconfigured, N-062); this server sends invitations and optional notices.
  test("the page says which mail uses it and which never does", async ({ page }) => {
    await page.goto(org("C", "email-settings"));
    const header = page.locator("main").first();
    await expect(header).toContainText(/invitations/i);
    await expect(page.getByText(/delivery for team invitations, password resets and security notices/i)).toHaveCount(0);
    await expect(page.getByTestId("org-email-scope")).toContainText(/password resets/i);
    await expect(page.getByTestId("org-email-scope")).toContainText(/platform/i);
    await expectHealthyPage(page);
  });
});

test.describe("Workspace AI settings", () => {
  test.use({ user: "C.owner" });

  // F-307: temperature reached only Ask AI answers (reading documents uses fixed, low
  // temperatures), and the two penalties never reach Gemini models. The page said neither.
  test("the page says what each tuning control reaches", async ({ page }) => {
    await page.goto(ws("C", "settings?section=ai"));
    await expect(page.locator("#ai-temperature")).toBeVisible();
    await expect(page.getByTestId("ai-temperature-scope")).toContainText(/ask ai/i);
    const provider = page.locator("#ai-provider");
    const offered = await provider.locator("option").evaluateAll((options) => options.map((o) => (o as HTMLOptionElement).value));
    if (offered.includes("GEMINI")) {
      await provider.selectOption("GEMINI");
      await expect(page.locator("#ai-frequency_penalty")).toHaveAttribute("readonly", "");
      await expect(page.getByTestId("ai-penalty-scope")).toContainText(/gemini/i);
    } else {
      await expect(page.getByTestId("ai-penalty-scope")).toContainText(/gemini/i);
    }
    await expectHealthyPage(page);
  });
});

test.describe("Organization identifier", () => {
  test.use({ user: "C.owner" });

  // F-308: the page said the identifier "is changed through support"; support has no such tool.
  test("the page does not promise a support path that does not exist", async ({ page }) => {
    await page.goto(org("C", "settings"));
    await expect(page.locator("#org-slug")).toBeVisible();
    await expect(page.getByText(/changed through support/i)).toHaveCount(0);
    await expect(page.getByText(/cannot be changed on this page/i)).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Profile time zone (F-318)", () => {
  test.use({ user: "C.member" });

  // Saving the profile re-sent the time zone on screen, and the server records any time zone it
  // is sent as the person's choice: renaming yourself pinned your clock to UTC when you had never
  // chosen one (you saw the workspace's), and the app re-rendered under the new clock, wiping the
  // "Saved" message (the CI flake in 14-configuration).
  test("saving a new display name sends only the name, and says it saved", async ({ page }) => {
    await page.goto(ws("C", "settings"));
    const name = page.locator("#profile-name");
    await expect(name).toBeVisible();
    const sent = page.waitForRequest((request) => request.method() === "PATCH" && /\/me\/profile$/.test(request.url()));
    await name.fill(`Cleo Member ${Date.now()}`);
    await page.getByRole("button", { name: "Save changes" }).click();
    const body = (await sent).postDataJSON() as Record<string, unknown>;
    expect(Object.keys(body)).toEqual(["display_name"]);
    await expect(page.locator("body")).toContainText(/saved/i, { timeout: 10_000 });
    await name.fill("Cleo Member");
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("body")).toContainText(/saved/i, { timeout: 10_000 });
  });
});

test.describe("Workspace clock and currency (F-320, F-321)", () => {
  test.use({ user: "C.owner" });

  // The workspace page offered ten time zones and ten currencies, while the server stored any
  // text it was sent. A workspace in Zurich could not choose its own clock or currency, and one
  // holding a value outside the ten could not save the page at all. A new workspace is created
  // here (through the product), so the shared workspaces' clocks are left alone.
  test("a new workspace takes any real zone and currency, and keeps them", async ({ page, session }) => {
    if (!session) {
      throw new Error("this test needs a signed-in owner");
    }
    const name = `Zurich ${Date.now()}`;
    await page.goto(`${org("C")}/workspaces/new`);
    await page.locator("#workspace_name").fill(name);
    await page.getByRole("button", { name: "Create workspace" }).click();
    await expect(page).toHaveURL(new RegExp(`/${TENANTS.C.org}/zurich-\\d+`), { timeout: 15_000 });
    const workspaceSlug = new URL(page.url()).pathname.split("/")[2]!;
    const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, workspaceSlug);

    try {
      await page.goto(`/${TENANTS.C.org}/${workspaceSlug}/settings`);
      await expect(async () => {
        await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
        await expect(page.getByRole("textbox", { name: "Workspace name" })).toHaveValue(name, { timeout: 3_000 });
      }).toPass({ timeout: 30_000 });

      const clock = page.locator("#timezone");
      await clock.fill("Mars/Olympus");
      await expect(clock).toHaveAttribute("aria-invalid", "true");
      await clock.fill("Europe/Zurich");
      await expect(clock).toHaveAttribute("aria-invalid", "false");
      await page.locator("#currency").selectOption("CHF");
      await expect(page.locator("#currency option:checked")).toHaveText(/Swiss Franc \(CHF\)/);

      const sent = page.waitForRequest((request) => request.method() === "PATCH" && request.url().endsWith(`/workspaces/${workspaceId}`));
      await page.getByRole("button", { name: "Save workspace" }).click();
      const body = (await sent).postDataJSON() as Record<string, unknown>;
      expect([body.timezone, body.currency]).toEqual(["Europe/Zurich", "CHF"]);
      // F-322: a new clock re-renders the workspace's pages; the person stays where they were.
      await expect(page.locator("body")).toContainText(/workspace settings saved/i, { timeout: 10_000 });
      await expect(page).toHaveURL(/[?&]section=workspace\b/);
      await expect(page.getByRole("button", { name: "Save workspace" })).toBeDisabled({ timeout: 10_000 });

      const stored = await api<{ timezone: string; currency: string }>(session, "GET", `/workspaces/${workspaceId}`);
      expect([stored.body.timezone, stored.body.currency]).toEqual(["Europe/Zurich", "CHF"]);
      await page.reload();
      await expect(page.locator("#timezone")).toHaveValue("Europe/Zurich", { timeout: 15_000 });
      await expect(page.locator("#currency")).toHaveValue("CHF");

      // The server refuses what it cannot honour, whoever sends it.
      const refused = await api(session, "PATCH", `/workspaces/${workspaceId}`, { timezone: "Mars/Olympus" });
      expect(refused.status).toBe(422);
    } finally {
      await api(session, "POST", `/workspaces/${workspaceId}/archive`);
    }
  });
});
