/**
 * Campaign session 4 (CAMPAIGN-MAP 4.3) — the public face: /pricing, /terms and /privacy.
 *
 * An anonymous visitor reads the plans the SERVER publishes (every price is compared with
 * GET /api/v1/pricing, never with a number typed here), sees the free trial stated with the
 * length the server enforces, and is sent to sign-up. The terms and privacy pages say they
 * are placeholders pending the owner's legal review and make no compliance claims (N-029).
 * All three are linked from the sign-in page, open signed in too, and fit a 375 px phone.
 *
 *   npx playwright test -c e2e/playwright.config.ts e2e/tests/70-public-pages.spec.ts
 */
import type { Page } from "@playwright/test";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api } from "../support/api";

interface PublicPlanPrice {
  interval: string;
  currency: string;
  unit_amount: number;
}

interface PublicPlan {
  key: string;
  display_name: string;
  per_seat: boolean;
  prices: PublicPlanPrice[];
  allowances: Array<{ key: string; limit_quantity: number | null }>;
  features: string[];
}

interface PublicPricing {
  trial: { plan_key: string; days: number };
  plans: PublicPlan[];
}

const PLACEHOLDER_BANNER = "Placeholder — pending the owner's legal review";
const TO_BE_COMPLETED = "[To be completed by the owner's legal counsel]";
/** Claims N-029 keeps off the legal pages until counsel writes them. */
const COMPLIANCE_CLAIMS = /\bGDPR\b|SOC ?2|HIPAA|ISO ?27001|99\.9|uptime|certified|compliant/i;
/** An internal key on a public page (plan rows, overage policies). */
const RAW_KEYS = /\b(capability|addon|limit|llm|storage|ocr|document|assistant)\.[a-z_]+\b|ALLOW_AND_|\bREFUSE\b/;

async function publishedPricing(): Promise<PublicPricing> {
  const response = await api<PublicPricing>(null, "GET", "/pricing");
  expect(response.status, response.text).toBe(200);
  return response.body;
}

/** The price a plan card lists: monthly, in US dollars when it is sold in them (Pricing.tsx). */
function listedPrice(plan: PublicPlan): PublicPlanPrice | null {
  const monthly = plan.prices.filter((price) => price.interval === "month");
  return monthly.find((price) => price.currency === "USD") ?? monthly[0] ?? plan.prices[0] ?? null;
}

/** "$49" for 4900 cents: the page's own rule (whole amounts without cents), in the browser's en-US. */
function money(price: PublicPlanPrice): string {
  const whole = price.unit_amount % 100 === 0;
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: price.currency,
    ...(whole ? { minimumFractionDigits: 0, maximumFractionDigits: 0 } : {}),
  }).format(price.unit_amount / 100);
}

async function documentFitsWidth(page: Page): Promise<{ doc: number; view: number }> {
  return page.evaluate(() => ({
    doc: document.documentElement.scrollWidth,
    view: document.documentElement.clientWidth,
  }));
}

test.describe("Pricing, for an anonymous visitor", () => {
  test("every plan the server publishes is listed at the server's price", async ({ page }) => {
    const pricing = await publishedPricing();
    expect(pricing.plans.length, "the e2e seed publishes the plans").toBeGreaterThan(0);

    await page.goto("/pricing");
    await expect(page.getByRole("heading", { level: 1, name: "Plans and pricing" })).toBeVisible();
    await expect(page.locator("h1")).toHaveCount(1);
    await expect(page.locator('[data-testid^="pricing-plan-"]')).toHaveCount(pricing.plans.length);

    for (const plan of pricing.plans) {
      const card = page.getByTestId(`pricing-plan-${plan.key}`);
      await expect(card.getByRole("heading", { level: 2, name: plan.display_name })).toBeVisible();

      const listed = listedPrice(plan);
      const price = page.getByTestId(`pricing-price-${plan.key}`);
      if (listed) {
        await expect(price).toHaveAttribute("data-amount", String(listed.unit_amount));
        await expect(price).toHaveAttribute("data-currency", listed.currency);
        await expect(price).toHaveAttribute("data-interval", listed.interval);
        await expect(price).toContainText(money(listed));
        if (listed.unit_amount > 0) {
          await expect(price).toContainText(`per seat / ${listed.interval}`);
        }
        const yearly = plan.prices.find((p) => p.interval === "year" && p.currency === listed.currency);
        if (listed.interval === "month" && yearly) {
          await expect(page.getByTestId(`pricing-yearly-${plan.key}`)).toContainText(money(yearly));
        }
      } else {
        await expect(price).toContainText("Price not published yet");
      }

      // What each plan includes: one line per allowance the server lists.
      await expect(card.getByRole("list", { name: `${plan.display_name} allowances` }).getByRole("listitem")).toHaveCount(
        plan.allowances.length,
      );
    }

    // Labels, never schema keys or enum values.
    expect(await page.locator("main").innerText()).not.toMatch(RAW_KEYS);
    await settle(page);
    await expectHealthyPage(page);
  });

  test("the free trial is stated with the length the server enforces", async ({ page }) => {
    const pricing = await publishedPricing();
    const { days, plan_key: trialPlan } = pricing.trial;
    test.skip(days <= 0, "the deployment runs without a trial");

    await page.goto("/pricing");
    await expect(page.getByTestId(`pricing-price-${trialPlan}`)).toContainText(`for ${days} days`);
    const trial = page.getByTestId("pricing-trial");
    await expect(trial.getByRole("heading", { name: "How the free trial works" })).toBeVisible();
    await expect(trial).toContainText(`one ${days}-day trial`);
    await expect(trial).toContainText("read-only until someone subscribes");
    await expect(page.locator("main")).toContainText(`Start with a ${days}-day free trial.`);
    await expectHealthyPage(page);
  });

  test("the calls to action go to sign-up", async ({ page }) => {
    const pricing = await publishedPricing();
    await page.goto("/pricing");
    for (const plan of pricing.plans) {
      await expect(page.getByTestId(`pricing-cta-${plan.key}`)).toHaveAttribute("href", "/register");
    }
    await expect(page.getByTestId(`pricing-cta-${pricing.trial.plan_key}`)).toHaveText(/Start free trial/);
    await page.getByTestId("pricing-cta-hero").click();
    await expect(page).toHaveURL(/\/register$/);
    await expect(page.getByRole("heading", { name: "Create your account" })).toBeVisible();
    await expectHealthyPage(page);
  });

  test("the pricing page links the terms and the privacy policy", async ({ page }) => {
    await page.goto("/pricing");
    await page.getByRole("main").getByRole("link", { name: "terms of service" }).click();
    await expect(page).toHaveURL(/\/terms$/);
    await page.goto("/pricing");
    await page.getByRole("main").getByRole("link", { name: "privacy policy" }).click();
    await expect(page).toHaveURL(/\/privacy$/);
    await expectHealthyPage(page);
  });
});

test.describe("Terms and privacy are placeholders pending the owner's legal review", () => {
  for (const target of [
    { path: "/terms", title: "Terms of service" },
    { path: "/privacy", title: "Privacy policy" },
  ]) {
    test(`${target.path} says it is a placeholder and claims nothing`, async ({ page }) => {
      await page.goto(target.path);
      await expect(page.getByRole("heading", { level: 1, name: target.title })).toBeVisible();
      await expect(page.locator("h1")).toHaveCount(1);
      await expect(page.getByTestId("legal-placeholder-banner")).toContainText(PLACEHOLDER_BANNER);
      expect(await page.getByRole("heading", { level: 2 }).count()).toBeGreaterThanOrEqual(8);
      const text = await page.locator("main").innerText();
      expect(text).toContain(TO_BE_COMPLETED);
      expect(text).not.toMatch(COMPLIANCE_CLAIMS);
      await expectHealthyPage(page);
    });
  }
});

test.describe("The public pages from the sign-in page", () => {
  test("the sign-in footer reaches pricing, terms and privacy", async ({ page }) => {
    for (const target of [
      { link: "Pricing", path: "/pricing", heading: "Plans and pricing" },
      { link: "Terms of service", path: "/terms", heading: "Terms of service" },
      { link: "Privacy policy", path: "/privacy", heading: "Privacy policy" },
    ]) {
      await page.goto("/login");
      const footer = page.getByRole("navigation", { name: "Pricing and legal" });
      await footer.getByRole("link", { name: target.link }).click();
      await expect(page).toHaveURL(new RegExp(`${target.path}$`));
      await expect(page.getByRole("heading", { level: 1, name: target.heading })).toBeVisible();
    }
    await expectHealthyPage(page);
  });

  test("the sign-up page links them too", async ({ page }) => {
    await page.goto("/register");
    const footer = page.getByRole("navigation", { name: "Pricing and legal" });
    for (const name of ["Pricing", "Terms of service", "Privacy policy"]) {
      await expect(footer.getByRole("link", { name })).toBeVisible();
    }
    await expectHealthyPage(page);
  });
});

test.describe("On a 375 px phone", () => {
  for (const path of ["/pricing", "/terms", "/privacy", "/login"]) {
    test(`${path} does not scroll sideways`, async ({ page }) => {
      await page.setViewportSize({ width: 375, height: 812 });
      await page.goto(path);
      await expect(page.locator("h1").first()).toBeVisible();
      await settle(page);
      const widths = await documentFitsWidth(page);
      expect(widths.doc).toBeLessThanOrEqual(widths.view);
      await expectHealthyPage(page);
    });
  }
});

test.describe("Signed in, the public pages still open", () => {
  test.use({ user: "C.owner" });

  test("pricing offers the app instead of sign-up; the legal pages show their banner", async ({ page }) => {
    await page.goto("/pricing");
    await expect(page.getByRole("heading", { level: 1, name: "Plans and pricing" })).toBeVisible();
    await expect(page.getByRole("banner").getByRole("link", { name: "Open the app" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Sign in" })).toHaveCount(0);
    await page.goto("/terms");
    await expect(page.getByTestId("legal-placeholder-banner")).toBeVisible();
    await page.goto("/privacy");
    await expect(page.getByTestId("legal-placeholder-banner")).toBeVisible();
    await settle(page);
    await expectHealthyPage(page);
  });
});
