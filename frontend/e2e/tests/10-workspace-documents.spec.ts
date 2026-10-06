/**
 * WORKSPACE and DOCUMENT INTELLIGENCE as the Enterprise owner (Tenant C):
 * overview, notifications, upload (single + batch) through real processing,
 * the document viewer, search, filters and delete.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { listWorkItems, loginAs, resolveWorkspaceId } from "../support/api";
import { STATE_FILE, ws } from "../support/env";
import { buildPdf, samplePath } from "../support/sample-docs";

test.use({ user: "C.owner" });

function uniquePdf(testInfo: { outputPath: (name: string) => string }, label: string): string {
  // A fresh PDF per run: identical bytes would be deduplicated by the server.
  const stamp = `${Date.now()}-${Math.floor(Math.random() * 1e6)}`;
  const file = testInfo.outputPath(`${label}-${stamp}.pdf`);
  fs.writeFileSync(
    file,
    buildPdf([[`INVOICE ${label}`, `Invoice Number: INV-E2E-${stamp}`, "Vendor: Acme Industrial Supplies Ltd", "Total Amount Due: 99.00 USD"]]),
  );
  return file;
}

/** Choose files in the overview's upload tray and start ingestion, as a user would. */
async function uploadFromOverview(page: import("@playwright/test").Page, files: string | string[]): Promise<void> {
  await page.goto(ws("C"));
  await page.locator('main input[type="file"]').first().setInputFiles(files);
  await expect(page.getByText(/Selected files/i)).toBeVisible();
  await page.getByRole("button", { name: "Start Ingestion" }).click();
}

async function waitForStatus(name: string, wanted: RegExp, timeoutMs = 120_000): Promise<string> {
  const session = await loginAs("C.owner");
  const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
  const deadline = Date.now() + timeoutMs;
  let status = "missing";
  while (Date.now() < deadline) {
    const items = await listWorkItems(session, workspaceId, "pageSize=100&page_size=100");
    const item = items.find((candidate) => String(candidate.original_filename ?? "").includes(name));
    status = String(item?.status ?? "missing");
    if (wanted.test(status)) return status;
    await new Promise((resolve) => setTimeout(resolve, 2_000));
  }
  return status;
}

test.describe("Workspace — overview", () => {
  test("KPI cards, recent activity and the quick upload zone", async ({ page }) => {
    await page.goto(ws("C"));
    for (const kpi of ["Total Documents", "Processed Today", "Processing", "Success Rate"]) {
      await expect(page.locator("main")).toContainText(kpi);
    }
    await expect(page.locator("main")).toContainText("Recent Activity");
    await expect(page.locator("main")).toContainText(/PROCESS COMPLETED\s*\S+\.(pdf|png)/i);
    await expect(page.locator('main input[type="file"]').first()).toBeAttached();
    await expectHealthyPage(page);
  });

  test("quick navigation from the sidebar reaches each core page", async ({ page }) => {
    await page.goto(ws("C"));
    for (const [link, text] of [
      ["Documents", /Documents Database/],
      ["AI Assistant", /AI Assistant/],
      ["Workflows", /Automation Dashboard/],
      ["Review queue", /Review/],
      ["Overview", /Recent Activity/],
    ] as const) {
      await page.getByRole("link", { name: link, exact: true }).click();
      await expect(page.locator("main")).toContainText(text);
    }
  });
});

test.describe("Workspace — notifications", () => {
  test("the notifications page lists processing events and links to the document", async ({ page }) => {
    await page.goto(ws("C", "notifications"));
    await expect(page.locator("main")).toContainText("Document processed successfully");
    const inspect = page.getByRole("link", { name: /inspect document/i }).or(page.getByRole("button", { name: /inspect document/i }));
    await inspect.first().click();
    await expect(page).toHaveURL(/\/work-items\/[0-9a-f-]{36}/);
    await expectHealthyPage(page);
  });

  test("the notification center opens, marks everything read and closes", async ({ page }) => {
    await page.goto(ws("C"));
    await page.getByRole("button", { name: "Open Notifications Center" }).click();
    await expect(page.getByText("Alert Center")).toBeVisible();
    const markAll = page.getByRole("button", { name: /mark all read/i });
    if (await markAll.isVisible()) {
      await markAll.click();
      await expect(markAll).toBeHidden();
    }
    await page.getByRole("button", { name: "Close notification tray" }).click();
    await expect(page.getByText("Alert Center")).toBeHidden();
  });

  test("notifications can be filtered by category and marked unread (N-020 item 2)", async ({ page }) => {
    await page.goto(ws("C", "notifications"));
    await settle(page);
    const categories = page.getByRole("tablist", { name: "Category" });
    for (const name of ["Documents", "Automation", "Email", "System", "Security", "All categories"]) {
      await categories.getByRole("tab", { name }).click();
      await expect(categories.getByRole("tab", { name })).toHaveAttribute("aria-selected", "true");
    }
    const first = page.getByRole("article").first();
    await expect(first).toBeVisible({ timeout: 15_000 });
    // Mark it read (if it is not), then unread again; the Unread tab follows.
    const markRead = first.getByRole("button", { name: "Mark as read" });
    if (await markRead.isVisible()) {
      await markRead.click();
      await expect(first.getByRole("button", { name: "Mark as unread" })).toBeVisible();
    }
    const title = (await first.getByRole("heading").textContent()) ?? "";
    await first.getByRole("button", { name: "Mark as unread" }).click();
    await expect(first.getByRole("button", { name: "Mark as read" })).toBeVisible();
    await page.getByRole("tablist", { name: "Read state" }).getByRole("tab", { name: "Unread" }).click();
    await expect(page.getByRole("article").filter({ hasText: title }).first()).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Documents — upload and processing", () => {
  test.setTimeout(180_000);

  test("single PDF upload is processed to COMPLETED and shows in the list", async ({ page }, testInfo) => {
    const file = uniquePdf(testInfo, "single");
    const name = file.split("/").pop() as string;
    await uploadFromOverview(page, file);
    expect(await waitForStatus(name, /COMPLETED/)).toBe("COMPLETED");
    await page.goto(ws("C", "work-items"));
    await page.getByPlaceholder("Search documents...").fill(name);
    await page.getByRole("button", { name: "Apply Filters" }).click();
    await expect(page.locator("main")).toContainText(name);
    await expect(page.locator("main")).toContainText(/completed/i);
  });

  test("a PNG image upload is accepted and processed (stub OCR)", async ({ page }, testInfo) => {
    // Images go through OCR; with ML_STUBS the text is a labelled stub.
    const png = testInfo.outputPath(`logo-${Date.now()}.png`);
    const bytes = fs.readFileSync(samplePath("logoPng"));
    // Append a harmless trailing chunk-free byte run so the hash differs per run.
    fs.writeFileSync(png, Buffer.concat([bytes, Buffer.from(`\n${Date.now()}`)]));
    await uploadFromOverview(page, png);
    const name = png.split("/").pop() as string;
    expect(await waitForStatus(name, /COMPLETED|FAILED/)).toBe("COMPLETED");
  });

  test("batch upload of three PDFs processes every file", async ({ page }, testInfo) => {
    const files = [uniquePdf(testInfo, "batch-a"), uniquePdf(testInfo, "batch-b"), uniquePdf(testInfo, "batch-c")];
    await uploadFromOverview(page, files);
    for (const file of files) {
      expect(await waitForStatus(file.split("/").pop() as string, /COMPLETED|FAILED/)).toBe("COMPLETED");
    }
  });

  test("an unsupported file type is refused with a clear message", async ({ page, problems }, testInfo) => {
    problems.allowHttp(/\/work-items|\/upload/, [400, 415, 422], "the server refuses a .txt upload");
    const file = testInfo.outputPath("notes.txt");
    fs.writeFileSync(file, "plain text is not an allowed document type");
    await page.goto(ws("C"));
    await page.locator('main input[type="file"]').first().setInputFiles(file);
    const start = page.getByRole("button", { name: "Start Ingestion" });
    if (await start.isVisible()) await start.click();
    await expect(page.locator("body")).toContainText(/not (a )?supported|not allowed|unsupported|only pdf|file type/i, {
      timeout: 15_000,
    });
  });
});

test.describe("Documents — list, viewer, search", () => {
  test.setTimeout(180_000);
  test("search, status filter and sort work on the documents table", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await page.getByPlaceholder("Search documents...").fill("INV-E2E-1001");
    await page.getByRole("button", { name: "Apply Filters" }).click();
    await expect(page.locator("main")).toContainText("invoice-INV-E2E-1001.pdf");
    await expect(page.locator("main")).not.toContainText("purchase-order-PO-E2E-5001.pdf");
    await page.getByPlaceholder("Search documents...").fill("");
    await page.locator("main select").first().selectOption({ label: "Completed" });
    await page.getByRole("button", { name: "Apply Filters" }).click();
    await expect(page.locator("main")).toContainText("purchase-order-PO-E2E-5001.pdf");
    await page.getByRole("button", { name: "Document Filename" }).click();
    await page.getByRole("button", { name: "File Size" }).click();
    await expectHealthyPage(page);
  });

  test("the document viewer shows metadata, OCR text and every tab", async ({ page }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const invoice = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("INV-E2E-1001"),
    );
    await page.goto(ws("C", `work-items/${invoice?.id}`));
    await expect(page.getByRole("heading", { name: "invoice-INV-E2E-1001.pdf" })).toBeVisible();
    await expect(page.locator("main")).toContainText("COMPLETED");
    await page.getByRole("button", { name: "OCR", exact: true }).click();
    await expect(page.locator("main")).toContainText("INV-E2E-1001");
    for (const tab of ["Entities", "Obligations", "ERP postings", "Chat", "Summary"]) {
      await page.getByRole("button", { name: tab, exact: true }).click();
      await settle(page, 300);
      await expectHealthyPage(page);
    }
  });

  test("the viewer shows the page image next to the extracted fields and text (side by side)", async ({ page }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const invoice = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("INV-E2E-1001"),
    );
    await page.goto(ws("C", `work-items/${invoice?.id}`));
    // The Document tab opens first: the rendered page beside the extracted fields.
    const pages = page.getByRole("region", { name: "Document pages" });
    await expect(pages.getByRole("img", { name: "Page 1 of the document" })).toBeVisible({ timeout: 20_000 });
    await expect(pages).toContainText("Page 1 of 1");
    await expect(page.getByRole("region", { name: "Extracted fields" })).toBeVisible();
    await pages.getByRole("button", { name: "Zoom in" }).click();
    await pages.getByRole("button", { name: "Zoom out" }).click();
    // The OCR tab puts the same page beside the text read from it.
    await page.getByRole("button", { name: "OCR", exact: true }).click();
    await expect(page.locator("main img[alt*='page' i]").first()).toBeVisible({ timeout: 10_000 });
    await expect(page.getByRole("region", { name: "Extracted text" })).toContainText("Invoice Number: INV-E2E-1001");
    await expectHealthyPage(page);
  });

  test("a document waiting in the review queue is corrected there, and the viewer says so", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", "needs extracted fields: run with E2E_LLM=1 (support/llm-mock.mjs)");
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const invoice = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("INV-E2E-1001"),
    );
    // Tenant C runs calibrated autonomy with no fitted model yet: every extraction waits for a person.
    await page.goto(ws("C", `work-items/${invoice?.id}`));
    const fields = page.getByRole("region", { name: "Extracted fields" });
    await expect(fields).toContainText("INV-E2E-1001", { timeout: 20_000 });
    await expect(fields.getByRole("note")).toContainText("waiting in the review queue");
    await expect(fields.getByRole("button", { name: /^Edit field/ })).toHaveCount(0);
    await fields.getByRole("link", { name: "Open the review queue" }).click();
    await expect(page).toHaveURL(/\/verification/);
    await expectHealthyPage(page);
  });

  test("a document can be deleted after confirmation", async ({ page }, testInfo) => {
    const file = uniquePdf(testInfo, "to-delete");
    const name = file.split("/").pop() as string;
    await uploadFromOverview(page, file);
    expect(await waitForStatus(name, /COMPLETED|FAILED/)).toBe("COMPLETED");
    await page.goto(ws("C", "work-items"));
    await page.getByPlaceholder("Search documents...").fill(name);
    await page.getByRole("button", { name: "Apply Filters" }).click();
    await expect(page.locator("main")).toContainText(name);
    await page.getByRole("button", { name: "Delete Document" }).first().click();
    const confirm = page.getByRole("alertdialog").or(page.getByRole("dialog"));
    await confirm.getByRole("button", { name: /delete/i }).click();
    await expect(page.locator("main")).not.toContainText(name, { timeout: 15_000 });
  });
});

test.describe("Document viewer — correcting a field (Business tenant)", () => {
  test.use({ user: "B.owner" });

  test("an extracted field can be corrected and saved from the viewer (N-020 item 7)", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", "needs extracted fields: run with E2E_LLM=1 (support/llm-mock.mjs)");
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const invoice = (state.documents.B.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("INV-E2E-1001"),
    );
    await page.goto(ws("B", `work-items/${invoice?.id}`));
    const fields = page.getByRole("region", { name: "Extracted fields" });
    await expect(fields).toContainText("INV-E2E-1001", { timeout: 20_000 });
    // Each field says where it is printed; showing it highlights the box on the page.
    await fields.getByRole("button", { name: "Show Invoice number on the page" }).click();
    await expect(page.locator("button[data-field='invoice_number'][data-active='true']").first()).toBeVisible();

    const correct = async (value: string) => {
      await fields.getByRole("button", { name: "Edit field Payment terms" }).click();
      await fields.getByRole("textbox", { name: "New value for Payment terms" }).fill(value);
      await fields.getByRole("textbox", { name: "Reason for the correction (optional)" }).fill("E2E correction");
      await fields.getByRole("button", { name: "Save correction" }).click();
      await expect(page.getByText("Payment terms corrected", { exact: false }).first()).toBeVisible();
      await expect(fields.locator("li[data-field='payment_terms']")).toContainText(value);
    };
    await correct("Net 45");
    await expect(fields.locator("li[data-field='payment_terms']")).toContainText("Corrected");
    await page.reload();
    await expect(page.getByRole("region", { name: "Extracted fields" }).locator("li[data-field='payment_terms']")).toContainText(
      "Net 45",
    );
    await correct("Net 30"); // put it back for the other tests
    await expectHealthyPage(page);
  });
});
