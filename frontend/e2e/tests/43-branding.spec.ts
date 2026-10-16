/**
 * Campaign session 3 (settings truthfulness, F-300, N-065) — an organization's brand colours show.
 *
 * `Organization > Branding` saves a primary, accent, background and text colour and a colour
 * scheme, previews them, and says "Custom branding is live on your verified domains". The
 * host-resolved manifest served them, yet no page read them: a sign-in page on a verified domain
 * showed the organization's name and logo in Servabo's blue.
 *
 * A verified domain cannot be pointed at this test stack, so the manifest a verified domain is
 * served (`GET /branding/manifest`, which the server tests prove returns the saved tokens for a
 * verified host only) is answered here with one organization's saved brand.
 */
import type { Page } from "@playwright/test";

import { test, expect } from "../support/fixtures";
import { ws } from "../support/env";

const BRAND = {
  brand_name: "Acme Ledger",
  primary_color: "#0f766e",
  accent_color: "#f59e0b",
  background_color: "#0b1220",
  foreground_color: "#f8fafc",
  color_scheme: "DARK",
  logo_url: null,
  favicon_url: null,
  support_email: "help@acme.example",
  has_custom_branding: true,
};

const asVerifiedDomain = async (page: Page): Promise<void> => {
  await page.route(/\/api\/v1\/branding\/manifest$/, (route) =>
    route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify(BRAND) }),
  );
};

const rgb = (value: string): [number, number, number] => {
  const match = value.match(/rgba?\((\d+),\s*(\d+),\s*(\d+)/);
  expect(match, `not an rgb() colour: ${value}`).not.toBeNull();
  return [Number(match![1]), Number(match![2]), Number(match![3])];
};

/** Colours go through HSL on the way to the page, so allow a rounding step per channel. */
const expectColour = (actual: string, hex: string): void => {
  const want = [1, 3, 5].map((at) => parseInt(hex.slice(at, at + 2), 16));
  const got = rgb(actual);
  got.forEach((channel, index) => expect(Math.abs(channel - want[index]!), `${actual} vs ${hex}`).toBeLessThanOrEqual(2));
};

const style = (page: Page, selector: string, property: string): Promise<string> =>
  page.locator(selector).first().evaluate((node, name) => getComputedStyle(node).getPropertyValue(name), property);

test.describe("Brand colours on a verified domain", () => {
  test("the sign-in page wears the organization's colours and scheme", async ({ page }) => {
    await asVerifiedDomain(page);
    await page.goto("/login");
    const signIn = page.getByRole("button", { name: /^continue$/i });
    await expect(signIn).toBeVisible();
    await expect(page.getByText("Acme Ledger").first()).toBeVisible();

    await expect.poll(() => style(page, "html", "--primary").then((value) => value.trim())).not.toBe("221 83% 53%");
    expectColour(await signIn.evaluate((node) => getComputedStyle(node).backgroundColor), BRAND.primary_color);
    expectColour(await style(page, "main", "background-color"), BRAND.background_color);
    expectColour(await style(page, "main", "color"), BRAND.foreground_color);
    await expect(page.locator("html")).toHaveClass(/\bdark\b/);
    // On a dark surface, links keep the brand's hue in a shade that reads on near-black.
    const signUp = page.getByRole("link", { name: /^sign up$/i });
    expectColour(await signUp.evaluate((node) => getComputedStyle(node).color), "#80efe6");
  });

  test("on the platform's own address the sign-in page keeps Servabo's colours", async ({ page }) => {
    await page.goto("/login");
    const signIn = page.getByRole("button", { name: /^continue$/i });
    await expect(signIn).toBeVisible();
    // Servabo's blue, 221 83% 48% since campaign session 4 (F-362: 53% failed WCAG AA contrast).
    expectColour(await signIn.evaluate((node) => getComputedStyle(node).backgroundColor), "#1555e0");
    expect((await page.locator("html").getAttribute("style")) ?? "").not.toContain("--primary");
  });
});

test.describe("Brand colours inside the app on a verified domain", () => {
  test.use({ user: "C.owner" });

  test("buttons and focus rings take the primary colour; light or dark stays the person's choice", async ({ page }) => {
    await asVerifiedDomain(page);
    await page.goto(ws("C"));
    await expect(page.locator("main").first()).toBeVisible();
    await expect.poll(() => style(page, "html", "--primary").then((value) => value.trim())).not.toBe("221 83% 53%");
    const probe = await page.evaluate(() => {
      const node = document.createElement("button");
      node.className = "bg-primary text-primary-foreground ring-ring";
      document.body.appendChild(node);
      const colour = getComputedStyle(node).backgroundColor;
      node.remove();
      return colour;
    });
    expectColour(probe, BRAND.primary_color);
    // The organization chose DARK for its sign-in pages; this person never chose dark.
    await expect(page.locator("html")).not.toHaveClass(/\bdark\b/);
  });
});
