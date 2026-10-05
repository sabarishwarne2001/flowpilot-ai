/**
 * CONFIGURATION and standalone tools as the Enterprise owner: settings, the
 * redaction studio, global search (Ctrl+K) and the workspace switcher.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { C_SECOND_WS, STATE_FILE, runId, ws } from "../support/env";

test.use({ user: "C.owner" });

/** Open Settings → General once the page has finished loading (the click can race hydration). */
async function openGeneral(page: import("@playwright/test").Page): Promise<void> {
  await expect(async () => {
    await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
    await expect(page.getByRole("textbox", { name: "Workspace Name" })).toHaveValue(/\S/, { timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}

test.describe("Settings", () => {
  test("profile: change the display name and save", async ({ page }) => {
    await page.goto(ws("C", "settings"));
    const name = page.locator("#profile-name");
    await name.fill(`Cara Owner ${runId()}`);
    await page.getByRole("button", { name: "Save changes" }).click();
    await expect(page.locator("body")).toContainText(/saved|updated/i, { timeout: 10_000 });
    await name.fill("Cara Owner");
    await page.getByRole("button", { name: "Save changes" }).click();
  });

  test("workspace general: rename, save, and rename back", async ({ page }) => {
    await page.goto(ws("C", "settings"));
    await openGeneral(page);
    const field = page.getByRole("textbox", { name: "Workspace Name" });
    await field.fill(`Operations ${runId()}`);
    await page.getByRole("button", { name: "Save Workspace" }).click();
    await expect(page.locator("body")).toContainText(/saved|updated/i, { timeout: 10_000 });
    await field.fill("Operations");
    await page.getByRole("button", { name: "Save Workspace" }).click();
    await expect(page.getByRole("button", { name: "Save Workspace" })).toBeDisabled({ timeout: 10_000 });
  });

  test("every settings section opens without errors", async ({ page }) => {
    await page.goto(ws("C", "settings"));
    for (const section of [
      /^Active sessions/,
      /^General Name, locale/,
      /^AI What runs/,
      /^Email Sender, relay/,
      /^Documents Extraction/,
      /^Profile Name, avatar/,
    ]) {
      await page.getByRole("button", { name: section }).click();
      await settle(page, 400);
      await expectHealthyPage(page);
    }
  });

  test("unsaved changes block navigation until confirmed", async ({ page }) => {
    await page.goto(ws("C", "settings"));
    await openGeneral(page);
    await page.getByRole("textbox", { name: "Workspace Name" }).fill(`Unsaved ${runId()}`);
    await expect(page.getByRole("button", { name: "Save Workspace" }), "the form knows it is dirty").toBeEnabled();
    await page.getByRole("link", { name: "Documents", exact: true }).click();
    const dialog = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await expect(dialog).toBeVisible();
    await dialog.getByRole("button", { name: /stay|cancel|keep editing/i }).click();
    await expect(page.getByRole("textbox", { name: "Workspace Name" })).toHaveValue(/Unsaved/);
    await page.getByRole("button", { name: "Reset" }).click();
  });
});

test.describe("Redaction studio", () => {
  test.setTimeout(180_000);

  test("start a redaction from a document; the studio opens with detection results", async ({ page }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const invoice = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("INV-E2E-1001"),
    );
    await page.goto(ws("C", `work-items/${invoice?.id}`));
    await page.getByRole("button", { name: "Redact" }).click();
    const profiles = page.getByRole("menu", { name: "Redaction profile" });
    await expect(profiles).toBeVisible();
    test.info().annotations.push({ type: "profiles", description: (await profiles.innerText()).replace(/\s+/g, " ") });
    await profiles.getByRole("menuitem").first().click();
    await expect(page).toHaveURL(/\/redactions\/[0-9a-f-]{36}/, { timeout: 60_000 });
    await settle(page, 1500);
    await expectHealthyPage(page);
    // The bank account number on the invoice is the PII the automated pass should find.
    await expect(page.locator("main")).toContainText(/IBAN|account|GB29|detected|finding/i, { timeout: 60_000 });
  });
});

test.describe("Global search (Ctrl+K)", () => {
  test("Ctrl+K opens the palette and Enter deep-links to the page", async ({ page }) => {
    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    await page.keyboard.press("Control+k");
    const palette = page.getByRole("dialog", { name: "Search pages" });
    await expect(palette).toBeVisible();
    await page.getByPlaceholder("Jump to a page…").fill("obligations");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/obligations$/);
    await page.keyboard.press("Control+k");
    await page.getByPlaceholder("Jump to a page…").fill("tolerance");
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(/\/procurement\/policies$/);
    await page.keyboard.press("Control+k");
    await page.keyboard.press("Escape");
    await expect(palette).toBeHidden();
  });

  test("searching an invoice number finds the document (requested capability)", async ({ page }) => {
    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    await page.getByRole("button", { name: /Search pages/ }).click();
    await page.getByPlaceholder("Jump to a page…").fill("INV-E2E-1001");
    await settle(page, 800);
    await expect(page.getByRole("dialog", { name: "Search pages" })).toContainText("invoice-INV-E2E-1001", {
      timeout: 5_000,
    });
  });
});

test.describe("Workspace switcher", () => {
  test("switch to Finance and back; each workspace only shows its own documents", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await expect(page.locator("main")).toContainText("invoice-INV-E2E-1001.pdf");
    await page.getByRole("button", { name: /Operations Caretakers Global Inc/ }).click();
    await page.getByRole("menuitem", { name: /Finance/ }).or(page.getByRole("option", { name: /Finance/ })).or(page.getByRole("button", { name: /^Finance/ })).first().click();
    await expect(page).toHaveURL(new RegExp(`/caretakers-global/${C_SECOND_WS}`));
    await page.getByRole("link", { name: "Documents", exact: true }).click();
    await expect(page.locator("main")).not.toContainText("invoice-INV-E2E-1001.pdf");
    await expectHealthyPage(page);
  });
});
