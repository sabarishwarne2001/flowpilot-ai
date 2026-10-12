/**
 * Campaign session 3 (settings truthfulness) — what a settings page says it does, it does.
 *
 * Each test here pins a sentence on a settings page to the behaviour behind it, after the page
 * said more than the product did.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org, ws } from "../support/env";

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
