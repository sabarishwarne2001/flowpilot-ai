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

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, listWorkItems, loginAs, resolveWorkspaceId } from "../support/api";
import { ws } from "../support/env";
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
    await expect(page.getByRole("note")).toContainText(`This file is identical to original-${id}.pdf`);
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
