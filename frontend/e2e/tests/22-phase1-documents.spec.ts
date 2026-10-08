/**
 * PHASE 1 (document intelligence), live defects found by driving the running stack.
 *
 * F-155: the Documents list counted every document whatever the filter, so a filtered list said
 *        "Page 1 of N" over a handful of matches and its later pages were empty.
 * F-156: "_" and "%" in the search box were LIKE wildcards and matched every document.
 * F-158: an upload that repeats a file already in the workspace is flagged and links the original.
 * F-160: one refused file in a multi-file upload stopped the others, dropped them from the list and
 *        surfaced as an unhandled promise rejection; viewers were offered an upload they cannot do.
 */
import fs from "node:fs";

import type { Page } from "@playwright/test";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, listWorkItems, loginAs, resolveWorkspaceId } from "../support/api";
import { STATE_FILE, ws } from "../support/env";
import { buildPdf } from "../support/sample-docs";

function stamp(): string {
  return `${Date.now().toString(36)}${Math.floor(Math.random() * 1e6).toString(36)}`;
}

function pdfFile(testInfo: { outputPath: (name: string) => string }, label: string): string {
  const file = testInfo.outputPath(`${label}.pdf`);
  fs.writeFileSync(file, buildPdf([[`PHASE1 ${label}`, `Reference: ${label}`]]));
  return file;
}

async function waitForDocument(name: string): Promise<Record<string, unknown>> {
  const session = await loginAs("C.owner");
  const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
  const deadline = Date.now() + 60_000;
  while (Date.now() < deadline) {
    const found = (await listWorkItems(session, workspaceId, `search=${encodeURIComponent(name)}`)).find(
      (item) => item.original_filename === name,
    );
    if (found) return found;
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(`${name} never appeared in the workspace`);
}

async function searchDocuments(page: Page, text: string): Promise<void> {
  await page.getByPlaceholder("Search documents...").fill(text);
  await page.getByRole("button", { name: "Apply Filters" }).click();
}

test.describe("Documents list filters (F-155, F-156)", () => {
  test.use({ user: "C.owner" });

  test("a filtered list pages over the matches only, and wildcards are text", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await searchDocuments(page, "INV-E2E-100");
    const rows = page.locator("main tbody tr");
    await expect(rows).toHaveCount(2);
    // Two matches fit on one page: no pager at all (it said "Page 1 of N" over every document).
    await expect(page.getByText(/^Page \d+ of \d+$/)).toHaveCount(0);

    await searchDocuments(page, "_");
    await expect(page.locator("main")).toContainText("Procurement_Policy.pdf");
    await expect(page.locator("main")).not.toContainText("invoice-INV-E2E-1001.pdf");

    await searchDocuments(page, "%");
    await expect(page.getByText("No documents found")).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Uploading from the Documents page (F-158, F-160)", () => {
  test.use({ user: "C.owner" });

  test("one refused file does not stop the others, and stays listed with its reason", async ({ page, problems }, testInfo) => {
    problems.allowHttp(/\/work-items$/, [400], "the corrupt PDF is refused by the server");
    const id = stamp();
    const corrupt = testInfo.outputPath(`corrupt-${id}.pdf`);
    fs.writeFileSync(corrupt, "%PDF-1.4 this is not really a pdf");
    const good = [pdfFile(testInfo, `good-a-${id}`), pdfFile(testInfo, `good-b-${id}`)];

    await page.goto(ws("C"));
    await page.locator('main input[type="file"]').first().setInputFiles([corrupt, ...good]);
    await expect(page.getByTestId("upload-queue-item")).toHaveCount(3);
    await page.getByRole("button", { name: "Start Ingestion" }).click();

    await expect(page.getByText("2 documents uploaded. Processing has started.")).toBeVisible();
    const left = page.getByTestId("upload-queue-item");
    await expect(left).toHaveCount(1);
    await expect(left).toContainText(`corrupt-${id}.pdf`);
    await expect(left.getByRole("alert")).toContainText(/could not be parsed/i);
    await expect(page.getByRole("button", { name: "Retry" })).toBeVisible();

    for (const file of good) {
      await waitForDocument(file.split("/").pop() as string);
    }
    // The overview refreshed on its own: the new documents are in Recent Activity without a reload.
    await expect(page.locator("main")).toContainText(`good-a-${id}.pdf`);
  });

  test("the Documents page has its own upload", async ({ page }, testInfo) => {
    const file = pdfFile(testInfo, `from-documents-${stamp()}`);
    await page.goto(ws("C", "work-items"));
    const toggle = page.getByRole("button", { name: "Upload documents", exact: true }).first();
    await toggle.click();
    await expect(page.getByRole("button", { name: "Close upload" })).toHaveAttribute("aria-expanded", "true");
    await page.locator('main input[type="file"]').first().setInputFiles(file);
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await expect(page.getByText("1 document uploaded. Processing has started.")).toBeVisible();
    await expect(page.locator("main tbody")).toContainText(file.split("/").pop() as string);
  });

  test("a second copy of a file is flagged and links the original", async ({ page }, testInfo) => {
    const id = stamp();
    const original = pdfFile(testInfo, `original-${id}`);
    const copy = testInfo.outputPath(`copy-${id}.pdf`);
    fs.copyFileSync(original, copy);

    await page.goto(ws("C", "work-items"));
    await page.getByRole("button", { name: "Upload documents" }).click();
    const picker = page.locator('main input[type="file"]').first();
    await picker.setInputFiles(original);
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await expect(page.getByText("1 document uploaded. Processing has started.")).toBeVisible();
    await picker.setInputFiles(copy);
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await expect(page.getByText(`"copy-${id}.pdf" is identical to "original-${id}.pdf"`, { exact: false })).toBeVisible();

    await searchDocuments(page, id);
    const copyRow = page.locator("main tbody tr", { hasText: `copy-${id}.pdf` });
    await copyRow.getByRole("link", { name: "Duplicate" }).click();
    await expect(page.getByRole("heading", { name: `original-${id}.pdf` })).toBeVisible();

    const created = await waitForDocument(`copy-${id}.pdf`);
    await page.goto(ws("C", `work-items/${created.id as string}`));
    await expect(page.getByRole("note").filter({ hasText: "Duplicate upload." })).toContainText(
      `This file is identical to original-${id}.pdf`,
    );
    await expectHealthyPage(page);
  });

  test("the upload tray states this workspace's own limits", async ({ page }) => {
    const session = await loginAs("C.owner");
    const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
    const settings = await api<{ max_upload_size: number }>(session, "GET", `/workspaces/${workspaceId}/document-settings/`);
    await page.goto(ws("C"));
    await expect(page.getByTestId("upload-limits")).toContainText(`Maximum ${settings.body.max_upload_size} MB per file`);
  });
});

test.describe("Viewers are not offered an upload (F-160)", () => {
  test.use({ user: "C.viewer" });

  test("no upload zone on the overview or the Documents page", async ({ page }) => {
    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    await expect(page.getByRole("button", { name: "Upload documents" })).toHaveCount(0);
    await page.goto(ws("C", "work-items"));
    await expect(page.locator("main")).toContainText("Documents Database");
    await expect(page.getByRole("button", { name: "Upload documents" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});

/**
 * F-164: plan-gated pages rendered their "included on the Business and Enterprise plans" lock while
 * the plan was still being read, so a paying customer saw an upgrade message flash before every
 * page. The entitlements request is slowed here to make the window visible.
 */
const LOCK_TEXT = /included on the [A-Za-z ]+plans?|Upgrade your plan|Change your plan/;

async function watchForLocks(page: Page): Promise<void> {
  await page.addInitScript((source) => {
    const pattern = new RegExp(source);
    const seen: string[] = [];
    (window as unknown as { __locks: string[] }).__locks = seen;
    new MutationObserver(() => {
      const match = pattern.exec(document.body?.innerText ?? "");
      if (match && !seen.includes(match[0])) seen.push(match[0]);
    }).observe(document, { subtree: true, childList: true, characterData: true });
  }, LOCK_TEXT.source);
  await page.route("**/entitlements**", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_500));
    await route.continue();
  });
}

test.describe("Plan-gated pages while the plan loads (F-164)", () => {
  test.use({ user: "C.owner" });

  test("an Enterprise workspace never sees a plan lock", async ({ page }) => {
    test.setTimeout(180_000);
    await watchForLocks(page);
    for (const sub of ["tables", "cases", "packet-splits", "extraction-memory", "entities", "obligations"]) {
      const answered = page.waitForResponse(/\/entitlements/);
      await page.goto(ws("C", sub));
      await answered;
      await settle(page, 600);
      await expect(page.getByTestId("capability-loading")).toHaveCount(0, { timeout: 20_000 });
      await expectHealthyPage(page);
      const seen = await page.evaluate(() => (window as unknown as { __locks: string[] }).__locks);
      expect(seen, `${sub} showed a plan lock while loading`).toEqual([]);
    }
  });
});

test.describe("Plan-gated pages on a plan without them (F-164)", () => {
  test.use({ user: "A.owner" });

  test("the Developer plan still sees the lock once its plan is read", async ({ page }) => {
    await watchForLocks(page);
    await page.goto(ws("A", "tables"));
    await expect(page.locator("main")).toContainText(/included on the Business and Enterprise plans/);
  });
});

/**
 * F-165: on a phone the closed navigation drawer kept its shadow (a grey strip down the left edge of
 *        every page) and its links stayed in the tab order and the accessibility tree as an open
 *        dialog, so Tab walked into navigation nobody could see.
 * F-166: on a phone the Documents table squeezed eight fixed-width columns into the screen: names
 *        cut to "po....", headers printed over each other.
 */
test.describe("Phone width (F-165, F-166)", () => {
  test.use({ user: "C.owner", viewport: { width: 390, height: 844 } });

  test("the closed navigation drawer is not reachable and casts no shadow", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await expect(page.locator("main")).toContainText("Documents Database");
    const drawer = page.locator('aside[aria-label="Navigation Menu"]');
    for (let i = 0; i < 12; i += 1) {
      await page.keyboard.press("Tab");
      const inside = await drawer.evaluate((element) => element.contains(document.activeElement));
      expect(inside, `Tab ${i + 1} moved focus into the closed drawer`).toBe(false);
    }
    const shadow = await drawer.evaluate((element) => getComputedStyle(element).boxShadow);
    // Tailwind's shadow-none computes to transparent zero-size shadows; no shadow may have any size.
    expect(/(?<![\d.])[1-9]\d*(?:\.\d+)?px/.test(shadow), `the closed drawer casts a shadow: ${shadow}`).toBe(false);

    await page.getByRole("button", { name: "Toggle Navigation Drawer" }).click();
    await expect(drawer.getByRole("link", { name: "Documents" })).toBeVisible();
  });

  test("documents read as a list with full names", async ({ page }) => {
    await page.goto(ws("C", "work-items"));
    await page.getByPlaceholder("Search documents...").fill("INV-E2E-1001");
    await page.getByRole("button", { name: "Apply Filters" }).click();
    const name = page.getByTestId("document-card").getByText("invoice-INV-E2E-1001.pdf");
    await expect(name).toBeVisible();
    const fits = await name.evaluate((element) => element.scrollWidth <= element.clientWidth + 1);
    expect(fits, "the file name is cut off").toBe(true);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  });
});

test.describe("Document workbench (Phase 1)", () => {
  test.use({ user: "C.owner" });

  test("fields carry verification confidence, pages turn from the keyboard", async ({ page }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const items = state.documents.C.items as Array<{ id: string; name: string }>;
    const invoice = items.find((item) => item.name.includes("INV-E2E-1001"));
    await page.goto(ws("C", `work-items/${invoice?.id}`));
    const fields = page.getByRole("region", { name: "Extracted fields" });
    await expect(fields.getByTestId("document-type")).toHaveText("Invoice");
    await expect(fields.getByTestId("document-confidence")).toContainText(/Verified \d+%/);
    await expect(fields.getByTestId("field-confidence").first()).toContainText(/\d+%/);
    await fields.getByRole("textbox", { name: "Find a field or value" }).fill("vendor");
    await expect(fields.locator("li[data-field]")).not.toHaveCount(0);
    await expect(fields.locator("li[data-field='invoice_number']")).toHaveCount(0);

    const packet = items.find((item) => item.name.includes("scanned-packet"));
    await page.goto(ws("C", `work-items/${packet?.id}`));
    const pages = page.getByRole("region", { name: "Document pages" });
    await expect(pages.getByRole("img", { name: "Page 1 of the document" })).toBeVisible({ timeout: 20_000 });
    await pages.getByRole("group", { name: /canvas/ }).focus();
    await page.keyboard.press("ArrowRight");
    await expect(pages.getByRole("img", { name: "Page 2 of the document" })).toBeVisible({ timeout: 20_000 });
    await page.keyboard.press("+");
    await expect(pages.getByRole("button", { name: /^Zoom 125%/ })).toBeVisible();
    await page.keyboard.press("0");
    await expect(pages.getByRole("button", { name: /^Zoom 100%/ })).toBeVisible();
    await pages.getByRole("textbox", { name: "Go to page" }).fill("4");
    await pages.getByRole("textbox", { name: "Go to page" }).press("Enter");
    await expect(pages.getByRole("img", { name: "Page 4 of the document" })).toBeVisible({ timeout: 20_000 });
    await expectHealthyPage(page);
  });
});
