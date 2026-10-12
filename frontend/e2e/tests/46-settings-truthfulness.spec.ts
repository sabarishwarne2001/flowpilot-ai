/**
 * Campaign session 3 (settings truthfulness) — what a settings page says it does, it does.
 *
 * Each test here pins a sentence on a settings page to the behaviour behind it, after the page
 * said more than the product did.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org } from "../support/env";

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
