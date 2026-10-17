/**
 * Campaign session 4 (CAMPAIGN-MAP 4.3) — the public face: /pricing, /terms and /privacy.
 *
 * Website session (B7): those pages moved to the public website (website/, servabo.app), which
 * publishes the plans from the same catalogue (backend/scripts/export_public_pricing.py, kept
 * current by tests/scripts/test_public_pricing_export_is_current.py) and is checked by the
 * website CI job (links, HTML, accessibility in both themes, Lighthouse). What the app still owns,
 * and this spec checks:
 *
 *   - the app's old addresses send visitors on to the website's page (old links keep working);
 *   - the sign-in and sign-up pages link the website's pricing, terms and privacy pages, and back
 *     to its home page; a white-labelled domain does not (it would name the vendor);
 *   - sign-up says what creating an account agrees to, with links to both documents;
 *   - the Billing page links the refund and cancellation policy.
 *
 * The bundle is built with VITE_WEBSITE_URL set to a local address (playwright.config.ts) and every
 * request to it is answered here, so no test reaches the real website.
 *
 *   npx playwright test -c e2e/playwright.config.ts e2e/tests/70-public-pages.spec.ts
 */
import type { Page } from "@playwright/test";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org } from "../support/env";

const WEBSITE = (process.env.E2E_WEBSITE_URL ?? "http://localhost:4321").replace(/\/+$/, "");

/** Answer the website's addresses with a stand-in page that names the path it was asked for. */
async function standInWebsite(page: Page): Promise<void> {
  await page.route(`${WEBSITE}/**`, (route) =>
    route.fulfill({
      status: 200,
      contentType: "text/html",
      body: `<!doctype html><title>website</title><h1>Website ${new URL(route.request().url()).pathname}</h1>`,
    }),
  );
}

test.describe("The app's old public addresses go to the website", () => {
  for (const target of [
    { path: "/pricing", to: "/pricing" },
    { path: "/terms", to: "/legal/terms" },
    { path: "/privacy", to: "/legal/privacy" },
  ]) {
    test(`${target.path} opens the website's ${target.to}`, async ({ page }) => {
      await standInWebsite(page);
      await page.goto(target.path);
      await expect(page).toHaveURL(`${WEBSITE}${target.to}`);
      await expect(page.getByRole("heading", { level: 1, name: `Website ${target.to}` })).toBeVisible();
    });
  }
});

test.describe("The website from the sign-in and sign-up pages", () => {
  test("the sign-in footer reaches the website's pricing, terms, privacy and home page", async ({ page }) => {
    await standInWebsite(page);
    for (const target of [
      { link: "Pricing", to: "/pricing" },
      { link: "Terms of service", to: "/legal/terms" },
      { link: "Privacy policy", to: "/legal/privacy" },
      { link: `Back to ${new URL(WEBSITE).host}`, to: "/" },
    ]) {
      await page.goto("/login");
      const footer = page.getByRole("navigation", { name: "Pricing and legal" });
      await footer.getByRole("link", { name: target.link }).click();
      await expect(page).toHaveURL(`${WEBSITE}${target.to}`);
    }
  });

  test("the sign-up page links them too, and says what creating an account agrees to", async ({ page }) => {
    await page.goto("/register");
    const footer = page.getByRole("navigation", { name: "Pricing and legal" });
    await expect(footer.getByRole("link", { name: "Pricing" })).toHaveAttribute("href", `${WEBSITE}/pricing`);
    await expect(footer.getByRole("link", { name: "Terms of service" })).toHaveAttribute("href", `${WEBSITE}/legal/terms`);
    await expect(footer.getByRole("link", { name: "Privacy policy" })).toHaveAttribute("href", `${WEBSITE}/legal/privacy`);

    const consent = page.getByTestId("register-consent");
    await expect(consent).toHaveText("By creating an account you agree to the Terms of Service and the Privacy Policy.");
    await expect(consent.getByRole("link", { name: "Terms of Service" })).toHaveAttribute("href", `${WEBSITE}/legal/terms`);
    await expect(consent.getByRole("link", { name: "Privacy Policy" })).toHaveAttribute("href", `${WEBSITE}/legal/privacy`);
    // The agreement comes before the button that makes it.
    const order = await page.evaluate(() => {
      const line = document.querySelector('[data-testid="register-consent"]');
      const submit = document.querySelector('form button[type="submit"]');
      return line && submit ? line.compareDocumentPosition(submit) & Node.DOCUMENT_POSITION_FOLLOWING : 0;
    });
    expect(order, "the consent line is above the Create account button").toBeTruthy();
    await expectHealthyPage(page);
  });
});

test.describe("Billing links the refund and cancellation policy", () => {
  test.use({ user: "C.owner" });

  test("the policy opens on the website, in a new tab", async ({ page }) => {
    await page.goto(org("C", "billing"));
    const line = page.getByTestId("billing-refund-policy");
    await expect(line).toContainText("14-day money-back guarantee");
    const link = line.getByRole("link", { name: "refund and cancellation policy" });
    await expect(link).toHaveAttribute("href", `${WEBSITE}/legal/refunds`);
    await expect(link).toHaveAttribute("target", "_blank");
    await expect(link).toHaveAttribute("rel", /noopener/);
    await expectHealthyPage(page);
  });
});

test.describe("On a 375 px phone", () => {
  for (const path of ["/login", "/register"]) {
    test(`${path}, with the website's links in its footer, does not scroll sideways`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 });
      await page.goto(path);
      await expect(page.getByRole("navigation", { name: "Pricing and legal" })).toBeVisible();
      const widths = await page.evaluate(() => ({
        doc: document.documentElement.scrollWidth,
        view: document.documentElement.clientWidth,
      }));
      expect(widths.doc).toBeLessThanOrEqual(widths.view);
      await expectHealthyPage(page);
    });
  }
});
