/**
 * DIALOGS AND THE KEYBOARD: Escape closes, focus goes in on open and back on close.
 *
 * ConfirmDialog always did; eleven other dialogs did none of it (API keys, analytics
 * destinations, workspace access, SCIM tokens, seats, compliance erasure, audit details,
 * supplier invoices, bulk delete, the assistant's pickers). Escape did nothing and Tab walked
 * out into the page behind the overlay. These open two of them the way a keyboard user would.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org, ws } from "../support/env";

test.use({ user: "C.owner" });

async function escapeCloses(page: import("@playwright/test").Page, trigger: import("@playwright/test").Locator) {
  await trigger.click();
  const dialog = page.getByRole("dialog").last();
  await expect(dialog).toBeVisible();
  // Focus moved into the dialog.
  await expect.poll(() => dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  // Tab stays inside it.
  for (let i = 0; i < 25; i += 1) {
    await page.keyboard.press("Tab");
  }
  expect(await dialog.evaluate((node) => node.contains(document.activeElement))).toBe(true);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
  // And back to the button that opened it.
  await expect(trigger).toBeFocused();
}

test("Compliance: the subject-erasure dialog", async ({ page }) => {
  await page.goto(org("C", "compliance"));
  await escapeCloses(page, page.getByRole("button", { name: "Erase a subject" }));
  await expectHealthyPage(page);
});

test("Workspace settings: adding a member", async ({ page }) => {
  await page.goto(ws("C", "settings"));
  await expect(async () => {
    await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
    await expect(page.getByRole("button", { name: "Add member" })).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
  await escapeCloses(page, page.getByRole("button", { name: "Add member" }));
  await expectHealthyPage(page);
});
