/**
 * THEME: light, dark and "follow the system".
 *
 * Three defects, all in how the theme is applied rather than in the colours:
 * - the header's toggle from "system" always went to light, so on a light-mode computer the
 *   first click did nothing and its icon showed the wrong choice;
 * - the theme was applied only once the app's JavaScript had loaded, so a dark-mode user saw a
 *   white page first on every load;
 * - (kept working) "system" follows the operating system when it changes.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { ws } from "../support/env";

test.use({ user: "C.owner" });

const isDark = (page: import("@playwright/test").Page) =>
  page.evaluate(() => document.documentElement.classList.contains("dark"));

test("on a light computer, the first click of the toggle switches to dark", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(ws("C"));
  await expect.poll(() => isDark(page)).toBe(false);
  const toggle = page.getByRole("button", { name: "Toggle Theme" });
  await expect(toggle).toHaveAttribute("title", /dark/i);
  await toggle.click();
  await expect.poll(() => isDark(page)).toBe(true);
  await expect(toggle).toHaveAttribute("title", /light/i);
  await toggle.click();
  await expect.poll(() => isDark(page)).toBe(false);
  await expectHealthyPage(page);
});

test("following the system: the page changes when the computer changes", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "light" });
  await page.goto(ws("C"));
  await expect.poll(() => isDark(page)).toBe(false);
  await page.emulateMedia({ colorScheme: "dark" });
  await expect.poll(() => isDark(page)).toBe(true);
  await page.emulateMedia({ colorScheme: "light" });
  await expect.poll(() => isDark(page)).toBe(false);
});

test("a dark computer gets a dark page before the app's code has loaded", async ({ page }) => {
  await page.emulateMedia({ colorScheme: "dark" });
  // Hold the application bundle back; whatever is painted meanwhile is what a slow load shows.
  let release: () => void = () => undefined;
  const held = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(/\/assets\/index-[^/]+\.js$/, async (route) => {
    await held;
    await route.continue();
  });
  await page.goto(ws("C"), { waitUntil: "commit" });
  await page.waitForTimeout(500);
  const darkBeforeApp = await isDark(page);
  release();
  expect(darkBeforeApp).toBe(true);
});
