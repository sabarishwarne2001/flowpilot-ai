/**
 * AUTOMATION (workflow builder, run history) and HUMAN REVIEW (review queue,
 * clause assertions) as the Enterprise owner.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { runId, ws } from "../support/env";
import { DISPUTED_INVOICE_PAGE, buildPdf } from "../support/sample-docs";
import { listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "../support/api";

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
    // Wait for a run to be listed. (Waiting for "No automation has run yet" to be absent also
    // passed while the page was still loading, and then found nothing to open.)
    const firstChain = page.locator("main").getByRole("button").filter({ hasNotText: "Blocked only" }).first();
    await expect(async () => {
      await page.reload();
      await expect(firstChain).toBeVisible({ timeout: 5_000 });
    }).toPass({ timeout: 120_000, intervals: [3_000] });
    // Open the first chain and its steps.
    await firstChain.click();
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
    // F-127: look for the packet-split item under its own tab. On "All" it sorts behind every
    // higher-severity item, and a database that has seen many runs (each adds a HIGH duplicate
    // finding for its disputed upload) pushes it to page 2.
    await page.getByRole("tab", { name: "Packet splits", exact: true }).click();
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

  test("a page emptied by resolving its items steps back instead of showing an empty queue", async ({ page }) => {
    // F-127. The queue is served from a stand-in so the page arithmetic is exact: 30 open
    // anomalies (two pages of 25), then the five on page 2 are confirmed in bulk.
    let open = 30;
    const synthetic = (n: number) => ({
      kind: "ANOMALY", item_id: `00000000-0000-4000-8000-${String(n).padStart(12, "0")}`, work_item_id: null,
      document_name: `synthetic-${n}.pdf`, headline: `Synthetic finding ${n}`, severity: "LOW", confidence: null,
      created_at: new Date().toISOString(), age_seconds: 60, status: "OPEN", assignee_user_id: null,
      assignee_email: null, under_retention_hold: false, tags: [], review_reason: "", version: 1, open_threads: 0,
    });
    await page.route(/\/api\/v1\/workspaces\/[^/]+\/review\?/, async (route) => {
      const asked = Number(new URL(route.request().url()).searchParams.get("page") ?? "1");
      const start = (asked - 1) * 25;
      const items = Array.from({ length: Math.max(0, Math.min(25, open - start)) }, (_, i) => synthetic(start + i + 1));
      await route.fulfill({ json: { items, total: open, page: asked, page_size: 25,
        counts_by_kind: { ANOMALY: open }, allowed_kinds: ["EXTRACTION", "ANOMALY"] } });
    });
    await page.route(/\/api\/v1\/workspaces\/[^/]+\/review\/bulk$/, async (route) => {
      const ids = (route.request().postDataJSON() as { ids: string[] }).ids;
      open -= ids.length;
      await route.fulfill({ json: { action: "resolve", kind: "ANOMALY", ok: ids.length, refused: 0, skipped: 0,
        results: ids.map((id) => ({ id, outcome: "ok" })) } });
    });

    await page.goto(ws("C", "verification"));
    const pager = page.getByRole("navigation", { name: "Pages" });
    await expect(pager).toContainText("page 1 of 2");
    await pager.getByRole("button", { name: "Next" }).click();
    await expect(pager).toContainText("page 2 of 2");
    const list = page.getByRole("list", { name: "Review items" });
    const unselected = list.getByRole("button", { name: "Select", exact: true });
    await expect(unselected).toHaveCount(5);
    for (let left = 5; left > 0; left -= 1) {
      await unselected.first().click(); // its label becomes "Deselect"
      await expect(unselected).toHaveCount(left - 1);
    }
    await page.getByRole("button", { name: "Confirm all" }).click();

    // 25 left: one page. The hub shows it rather than an empty page 2 with no pager to leave by.
    await expect(list).toContainText("Synthetic finding 1");
    await expect(page.getByText("Nothing is waiting for review here.")).toHaveCount(0);
    await expectHealthyPage(page);
  });

  test("bulk selection offers batch actions", async ({ page }) => {
    await page.goto(ws("C", "verification"));
    await page.getByRole("button", { name: "Select" }).first().click();
    await expect(page.locator("main")).toContainText(/1 selected|selected/i);
    await expectHealthyPage(page);
  });

  test("a disputed extraction is decided against the page, with each agent's reading boxed", async ({ page }, testInfo) => {
    test.skip(process.env.E2E_LLM !== "1", "needs model output: run with E2E_LLM=1 (support/llm-mock.mjs)");
    test.setTimeout(180_000);
    // A fresh copy of the invoice whose bank details changed: the cautious verification agent
    // refuses to read the account, so the agents disagree and it goes to a person (F-063).
    const name = `disputed-${runId()}.pdf`;
    const file = testInfo.outputPath(name);
    fs.writeFileSync(file, buildPdf([[...DISPUTED_INVOICE_PAGE, `Reference: ${name}`]]));
    const session = await loginAs("C.owner");
    const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
    expect((await uploadFile(session, workspaceId, file)).status).toBeLessThan(300);
    await expect
      .poll(async () => {
        const items = await listWorkItems(session, workspaceId, "pageSize=100&page_size=100");
        return String(items.find((item) => String(item.original_filename) === name)?.status ?? "missing");
      }, { timeout: 120_000 })
      .toBe("COMPLETED");

    await page.goto(ws("C", "verification"));
    await page.getByRole("tab", { name: "Extraction", exact: true }).click();
    await settle(page);
    await expect(page).toHaveURL(/view=EXTRACTION/);
    // Only the extraction item: the radar also (rightly) flags the copy's invoice number as a duplicate.
    const row = page
      .getByRole("list", { name: "Review items" })
      .getByRole("listitem")
      .filter({ hasText: name })
      .filter({ hasText: "Agents disagreed" });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row.getByRole("button", { name: /Extracted fields disagree/ }).click();
    const evidence = row.getByRole("region", { name: "Where the values are printed" });
    await expect(evidence.getByRole("img", { name: /Page 1 of the document/ })).toBeVisible({ timeout: 20_000 });
    await expect(evidence.getByRole("combobox", { name: "Field to show on the page" })).toHaveValue("vendor_bank_account");
    await expect(evidence).toContainText("not printed on the page"); // the agent that answered null
    await row.getByRole("button", { name: "Edit", exact: true }).click();
    await row.getByRole("textbox", { name: "Value for vendor_bank_account" }).fill("GB94 BARC 1020 1530 0934 59");
    await row.getByRole("button", { name: "Submit corrections" }).click();
    await expect(row).toHaveCount(0, { timeout: 15_000 });
    // Calibration holds are not disagreements: they are listed under Autonomy audits (F-111).
    await page.getByRole("tab", { name: "Autonomy audits", exact: true }).click();
    await expect(page.getByRole("list", { name: "Review items" })).toContainText("Held for review: confirm every field");
    await expectHealthyPage(page);
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
