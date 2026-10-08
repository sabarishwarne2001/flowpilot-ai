/**
 * PHASE 2 (enterprise processing), live defects found by driving the running stack.
 *
 * F-171: a matching case that compared no line at all was MATCHED, and "Approve match" approved it
 *        on no evidence.
 * F-172: the matching queue printed every variance in rupees (the currency was a constant).
 * F-173: INV-E2E-1002 asks to be paid into a new account and Payment risk said no invoice changed
 *        its bank account: the check did not read `vendor_bank_account`. (12-processing's test of the
 *        same name only checked that the radar was not empty, which the duplicate finding satisfied.)
 * F-174: the queue and the radar showed the internal vendor key ("name:acme industrial supplies").
 * F-175: assistant sources have no id; every source shared one React key (a console error on each
 *        answer in development) and the citation drawer printed an empty "Citation ID". The passage
 *        sat in a nested scroll box that collapsed on short screens.
 * F-176: the extraction workbench named documents by their id's first eight characters.
 * F-178: with an extraction item open in the review hub, "a" (the hub's "assign to me") also
 *        reached the embedded workbench, whose "a" is "accept all": one key assigned the item and
 *        approved every value the agents disagreed on. "e" (collapse) also switched it to editing.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "../support/api";
import { runId, ws } from "../support/env";
import { DISPUTED_INVOICE_PAGE, buildPdf } from "../support/sample-docs";

test.describe("Three-way matching (F-171, F-172, F-174)", () => {
  test.use({ user: "C.owner" });

  test("the queue names the vendor as printed and states amounts in the case's currency", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    await settle(page);
    const table = page.locator("main table");
    await expect(table).toBeVisible();
    await expect(table).toContainText("Acme Industrial Supplies Ltd");
    await expect(table).not.toContainText("name:acme");
    // The sample documents are in US dollars: no rupee sign anywhere on the page.
    await expect(page.locator("main")).not.toContainText("₹");
    await expect(table).toContainText("$");
    // Each row says which documents were matched, by the numbers they print.
    await expect(table).toContainText("INV-E2E-1001");
    await expect(table).toContainText("PO-E2E-5001");
    await expectHealthyPage(page);
  });

  test("a case that compared no line asks for a reason before it can be approved", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    await settle(page);
    const row = page.locator("main tbody tr").filter({ hasText: "INV-E2E-1001" }).first();
    await row.getByRole("link", { name: "Review" }).click();
    await expect(page.getByRole("heading", { name: /Three-way match/ })).toBeVisible();
    await expect(page.getByRole("region", { name: "Documents in this case" })).toContainText("PO-E2E-5001");
    await page.getByRole("button", { name: "Approve match" }).click();
    // No approval happens on the click: the reason dialog opens instead.
    await expect(page.getByRole("heading", { name: "Approve without a line-by-line comparison" })).toBeVisible();
    await expect(page.getByRole("textbox", { name: "Override reason" })).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();
    await expect(page.locator("main")).not.toContainText(/approved/i);
  });
});

test.describe("Forensic audit radar (F-173, F-174)", () => {
  test.use({ user: "C.owner" });

  test("the new payee account on INV-E2E-1002 is a payment-risk flag", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    await settle(page);
    const panel = page.locator("section, div").filter({ has: page.getByText("Payment risk", { exact: true }) }).first();
    await expect(panel).not.toContainText("No invoice changed its bank account");
    // Only the last four characters of either account are ever shown.
    await expect(page.locator("main")).toContainText("•••• 3459");
    await expect(page.locator("main")).toContainText("•••• 6819");
  });

  test("a duplicate's vendor evidence reads as a name, not a key", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    await settle(page);
    const finding = page.getByText(/carries the same document number/).first();
    await expect(finding).toBeVisible();
    await finding.click();
    await expect(page.locator("main")).toContainText("Acme Industrial Supplies");
    await expect(page.locator("main")).not.toContainText("name:acme");
  });
});

const LLM_AVAILABLE = process.env.E2E_LLM === "1";

test.describe("Assistant citations (F-175)", () => {
  test.use({ user: "C.owner" });
  test.setTimeout(150_000);

  async function askAndOpenFirstSource(page: import("@playwright/test").Page) {
    await page.goto(ws("C", "assistant"));
    await page.getByRole("button", { name: "New", exact: true }).click();
    const box = page.getByPlaceholder("Ask anything about your knowledge base...");
    await box.fill("What is the total amount due on invoice INV-E2E-1001?");
    await page.getByRole("button", { name: "Send message" }).click();
    const first = page.getByRole("button", { name: "Open citation 1" }).first();
    await expect(first).toBeVisible({ timeout: 90_000 });
    await first.click();
    const drawer = page.getByTestId("citation-drawer");
    await expect(drawer).toBeVisible();
    return drawer;
  }

  for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 700 }]) {
    test(`the cited passage reads at full width and sources can be walked (${viewport.width}px)`, async ({ page }) => {
      test.skip(!LLM_AVAILABLE, "needs the model stand-in (E2E_LLM=1) to answer with sources");
      await page.setViewportSize(viewport);
      const drawer = await askAndOpenFirstSource(page);
      const passage = page.getByTestId("citation-passage");
      await expect(passage).toContainText("INV-E2E-1001");
      const [drawerBox, passageBox] = await Promise.all([drawer.boundingBox(), passage.boundingBox()]);
      expect(drawerBox && passageBox, "drawer and passage are laid out").toBeTruthy();
      // The passage keeps (nearly) the drawer's width: no squeezed column breaking letter by letter.
      expect(passageBox!.width).toBeGreaterThan(drawerBox!.width * 0.75);
      // Lines hold words, not single letters: a 30-character line is at least 150px wide.
      expect(passageBox!.width).toBeGreaterThan(150);
      await expect(drawer).toContainText(/Source 1 of \d+/);
      await expect(drawer).not.toContainText("Citation ID");
      const title = await page.locator("#citation-drawer-title").innerText();
      await page.getByRole("button", { name: "Next source" }).click();
      await expect(drawer).toContainText(/Source 2 of \d+/);
      await expect(page.locator("#citation-drawer-title")).not.toHaveText("");
      test.info().annotations.push({ type: "sources", description: `${title} -> ${await page.locator("#citation-drawer-title").innerText()}` });
      await page.keyboard.press("Escape");
      await expect(drawer).toBeHidden();
    });
  }
});

test.describe("Review queue (F-176)", () => {
  test.use({ user: "C.owner" });

  test("the extraction workbench names the document by its file", async ({ page }) => {
    test.skip(!LLM_AVAILABLE, "needs the model stand-in (E2E_LLM=1) for a disagreed extraction");
    await page.goto(ws("C", "verification"));
    await settle(page);
    await page.getByText("Extracted fields disagree").first().click();
    const workbench = page.locator("aside").filter({ has: page.getByRole("heading", { name: /^Review queue/ }) }).first();
    await expect(workbench).toContainText("invoice-INV-E2E-1002.pdf");
    await expect(page.getByRole("heading", { name: "invoice-INV-E2E-1002.pdf" })).toBeVisible();
    await expect(page.getByRole("heading", { name: /^Document [0-9a-f]{8}$/ })).toHaveCount(0);
  });
});

test.describe("Review hub keyboard (F-178)", () => {
  test.use({ user: "C.owner" });

  test("'a' on an open extraction item assigns it and decides nothing", async ({ page }, testInfo) => {
    test.skip(!LLM_AVAILABLE, "needs the model stand-in (E2E_LLM=1) for a disagreed extraction");
    test.setTimeout(180_000);
    // A fresh copy of the invoice whose bank details changed, so the agents disagree on it.
    const name = `keys-${runId()}.pdf`;
    const file = testInfo.outputPath(name);
    fs.writeFileSync(file, buildPdf([[...DISPUTED_INVOICE_PAGE, `Reference: ${name}`]]));
    const session = await loginAs("C.owner");
    const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
    expect((await uploadFile(session, workspaceId, file)).status).toBeLessThan(300);
    await expect
      .poll(async () => {
        const items = await listWorkItems(session, workspaceId, "limit=100");
        return String(items.find((item) => String(item.original_filename) === name)?.status ?? "missing");
      }, { timeout: 120_000 })
      .toBe("COMPLETED");

    const decisions: string[] = [];
    page.on("request", (request) => {
      if (request.method() !== "GET" && /\/verifications\/[^/]+\/resolve/.test(request.url())) {
        decisions.push(request.url());
      }
    });
    await page.goto(ws("C", "verification"));
    await page.getByRole("tab", { name: "Extraction", exact: true }).click();
    await settle(page);
    const row = page
      .getByRole("list", { name: "Review items" })
      .getByRole("listitem")
      .filter({ hasText: name })
      .filter({ hasText: "Agents disagreed" });
    await expect(row).toBeVisible({ timeout: 30_000 });
    await row.getByRole("button", { name: /Extracted fields disagree/ }).click();
    await expect(row.getByRole("button", { name: "Accept all" })).toBeVisible();

    // Focus nothing that takes text, then press the hub's "assign to me".
    await page.getByRole("heading", { name: "Review", level: 1 }).click();
    await page.keyboard.press("a");
    await settle(page, 1500);
    expect(decisions, "no value was accepted by the key").toEqual([]);
    await page.reload();
    await page.getByRole("tab", { name: "Extraction", exact: true }).click();
    await settle(page);
    await expect(row).toBeVisible();

    // Tidy up: decide it on purpose, with the button.
    await row.getByRole("button", { name: /Extracted fields disagree/ }).click();
    await row.getByRole("button", { name: "Accept all" }).click();
    await expect(row).toHaveCount(0, { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});
