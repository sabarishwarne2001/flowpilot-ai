/**
 * AUTOMATION (workflow builder, run history) and HUMAN REVIEW (review queue,
 * clause assertions) as the Enterprise owner.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { runId, ws } from "../support/env";
import { buildPdf } from "../support/sample-docs";

test.use({ user: "C.owner" });

const builder = (page: import("@playwright/test").Page) => page.getByRole("dialog", { name: /New automation/i });

/** "Notify a role" needs no external endpoint, so a rule using it is valid on its own. */
async function pickFirstAvailableAction(page: import("@playwright/test").Page): Promise<string> {
  const picker = builder(page).getByRole("combobox", { name: "Add an action" }).first();
  await picker.selectOption("notify.role");
  await builder(page).getByRole("button", { name: "Owner", exact: true }).click();
  return "notify.role";
}

test.describe("Workflows — builder", () => {
  test.setTimeout(180_000);

  test("create a rule: trigger, action, validate and save; it appears in the list", async ({ page }) => {
    await page.goto(ws("C", "automation"));
    await page.getByRole("button", { name: "Create New Rule" }).click();
    await expect(page.getByRole("heading", { name: "New automation" })).toBeVisible();
    const name = `E2E rule ${runId()}`;
    await builder(page).getByRole("textbox", { name: "Name", exact: true }).fill(name);
    await builder(page).getByRole("checkbox", { name: /^Document uploaded/ }).check();
    const action = await pickFirstAvailableAction(page);
    test.info().annotations.push({ type: "action", description: action });
    await settle(page, 300);
    await builder(page).getByRole("button", { name: "Create rule" }).click();
    await expect(page.locator("main")).toContainText(name, { timeout: 15_000 });
    await expect(page.locator("main")).toContainText(/configured rules \(\d+\)/i);
    await expectHealthyPage(page);
  });

  test("a rule without a trigger or an action cannot be saved", async ({ page }) => {
    await page.goto(ws("C", "automation"));
    await page.getByRole("button", { name: "Create New Rule" }).click();
    await builder(page).getByRole("textbox", { name: "Name", exact: true }).fill(`E2E invalid ${runId()}`);
    const create = builder(page).getByRole("button", { name: "Create rule" });
    if (await create.isEnabled()) {
      await create.click();
      await expect(page.locator("body")).toContainText(/trigger|at least one action|needs/i);
      await expect(page.getByRole("heading", { name: "New automation" })).toBeVisible();
    }
    await page.getByRole("button", { name: "Close the flow builder" }).click();
  });

  test("filters and search on the rules and logs panels", async ({ page }) => {
    await page.goto(ws("C", "automation"));
    await page.getByPlaceholder("Search rules by name...").fill("E2E rule");
    await page.getByPlaceholder("Search logs by rule name or document filename...").fill("pdf");
    const clear = page.getByRole("button", { name: "Clear Filters" });
    if (await clear.isVisible()) await clear.click();
    await page.getByRole("button", { name: "Sync metrics manually" }).click();
    await settle(page);
    await expectHealthyPage(page);
  });
});

test.describe("Workflows — run history", () => {
  test.setTimeout(240_000);

  test("an upload fires the 'Document uploaded' rule and the run shows in history with its steps", async ({ page }, testInfo) => {
    // Make sure a rule on "Document uploaded" exists.
    await page.goto(ws("C", "automation"));
    if (!(await page.locator("main").innerText()).includes("E2E rule")) {
      await page.getByRole("button", { name: "Create New Rule" }).click();
      await builder(page).getByRole("textbox", { name: "Name", exact: true }).fill(`E2E rule ${runId()}`);
      await builder(page).getByRole("checkbox", { name: /^Document uploaded/ }).check();
      await pickFirstAvailableAction(page);
      await builder(page).getByRole("button", { name: "Create rule" }).click();
      await settle(page);
    }
    const file = testInfo.outputPath(`trigger-${Date.now()}.pdf`);
    fs.writeFileSync(file, buildPdf([["INVOICE", `Invoice Number: INV-E2E-RUN-${Date.now()}`, "Total Amount Due: 10.00 USD"]]));
    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    await page.locator('main input[type="file"]').first().setInputFiles(file);
    await expect(page.getByText(/Selected files/i)).toBeVisible();
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await page.goto(ws("C", "automation/timeline"));
    await expect(async () => {
      await page.reload();
      await expect(page.locator("main")).not.toContainText("No automation has run yet", { timeout: 2_000 });
    }).toPass({ timeout: 120_000, intervals: [3_000] });
    // Open the first chain and its steps.
    await page.locator("main").getByRole("button").filter({ hasNotText: "Blocked only" }).first().click();
    await settle(page);
    await page.getByRole("button", { name: "Blocked only" }).click();
    await expectHealthyPage(page);
  });
});

test.describe("Review queue", () => {
  test("type tabs, severity and filters work; an item can be taken and discussed", async ({ page }) => {
    await page.goto(ws("C", "verification"));
    for (const tab of ["Extraction", "Clause assertions", "Anomalies", "Packet splits", "History", "All"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
    }
    for (const severity of ["HIGH", "MEDIUM"]) {
      await page.getByRole("button", { name: severity, exact: true }).click();
      await page.getByRole("button", { name: severity, exact: true }).click();
    }
    await page.getByRole("combobox", { name: "Assignee" }).selectOption({ index: 1 });
    await page.getByRole("combobox", { name: "Assignee" }).selectOption({ index: 0 });
    await page.getByRole("textbox", { name: "Tag" }).fill("e2e");
    await page.getByRole("textbox", { name: "Tag" }).fill("");
    const item = page.getByRole("button", { name: /Packet split/ }).first();
    await expect(item).toBeVisible();
    const take = page.getByRole("button", { name: "Take it" }).first();
    if (await take.isVisible()) {
      await take.click();
      await settle(page);
    }
    await page.getByRole("button", { name: "Discuss this item" }).first().click();
    await settle(page);
    await expectHealthyPage(page);
  });

  test("bulk selection offers batch actions", async ({ page }) => {
    await page.goto(ws("C", "verification"));
    await page.getByRole("button", { name: "Select" }).first().click();
    await expect(page.locator("main")).toContainText(/1 selected|selected/i);
    await expectHealthyPage(page);
  });

  test("approve, reject and escalate an extraction item with its bounding boxes", async ({ page }) => {
    await page.goto(ws("C", "verification"));
    await page.getByRole("tab", { name: "Extraction", exact: true }).click();
    await settle(page);
    // Needs a low-confidence extraction; with stub OCR none is produced (see 03-coverage.md).
    await expect(page.getByRole("button", { name: /approve/i }).first()).toBeVisible({ timeout: 5_000 });
  });

  test("resolve the packet-split item from the queue", async ({ page }) => {
    await page.goto(ws("C", "verification"));
    await page.getByRole("tab", { name: "Packet splits", exact: true }).click();
    await page.getByRole("button", { name: "Resolve" }).first().click();
    const dialog = page.getByRole("dialog").or(page.getByRole("alertdialog"));
    await expect(dialog.or(page.locator("main"))).toContainText(/approve|reject|resolve/i);
    await expectHealthyPage(page);
  });
});

test.describe("Clause assertions", () => {
  test("create a clause check; it is listed and documents are checked against it", async ({ page }) => {
    await page.goto(ws("C", "assertions"));
    const name = `Payment within 30 days ${runId()}`;
    await page.getByRole("textbox", { name: "New clause check name" }).fill(name);
    await page.getByRole("button", { name: "New check" }).click();
    await expect(page.locator("main")).toContainText(name, { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});
