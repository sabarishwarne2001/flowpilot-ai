/**
 * BATCH OPERATIONS (Phase 1): batches, confidence analytics, schema health, dispatch lanes and
 * SHA-256 verified export packages, driven through the real UI against the real API and worker.
 *
 * Enterprise owner (Tenant C): create a batch from the Documents page selection, see its lanes and
 * analytics, dispatch it, build an export package on the worker, download it, verify it (and see a
 * file that is not a package rejected). A viewer reads but does not act. The Developer plan sees the
 * lock and the server refuses it.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId } from "../support/api";
import { ws } from "../support/env";

test.describe("Batch operations — Enterprise owner", () => {
  test.use({ user: "C.owner" });
  test.setTimeout(240_000);

  test("from a selection to a verified export package", async ({ page }, testInfo) => {
    // 1. Select the two sample invoices on the Documents page and add them to a new batch.
    await page.goto(ws("C", "work-items"));
    await page.getByPlaceholder("Search documents...").fill("INV-E2E-100");
    await page.getByRole("button", { name: "Apply Filters" }).click();
    await expect(page.locator("main tbody tr")).toHaveCount(2);
    await page.getByLabel("Select every document on this page").check();
    await page.getByRole("button", { name: "Add to batch" }).click();
    const name = `E2E invoices ${Date.now()}`;
    const dialog = page.getByRole("dialog", { name: "Add to a batch" });
    await dialog.getByPlaceholder("e.g. October supplier invoices").fill(name);
    await dialog.getByRole("button", { name: /Create batch/ }).click();
    await page.waitForURL(/\/batches\/[0-9a-f-]{36}$/);

    // 2. The batch: progress, lanes with reasons, analytics, schema health, documents.
    await expect(page.getByRole("heading", { name })).toBeVisible();
    await expect(page.getByTestId("kpi-progress")).toContainText("2 / 2");
    const review = page.getByTestId("lane-REVIEW");
    await expect(review).toContainText("invoice-INV-E2E-1001.pdf");
    await expect(review).toContainText(/review|calibration|disagree/i);
    await expect(page.getByTestId("confidence-histogram")).toBeVisible();
    await expect(page.getByTestId("field-reliability")).toContainText("total_amount");
    await expect(page.getByTestId("batch-documents").locator("tbody tr")).toHaveCount(2);
    await expect(page.getByTestId("schema-health")).toBeVisible();

    // 3. Dispatch records the lanes.
    await page.getByRole("button", { name: "Dispatch", exact: true }).click();
    await expect(page.getByText(/^Dispatched: \d+ straight through, \d+ to review, \d+ exceptions/)).toBeVisible();

    // 4. An export package, built by the worker.
    await page.getByRole("button", { name: "New package" }).click();
    await page.getByRole("button", { name: "Build package" }).click();
    const ready = page.locator('[data-testid="package-row"][data-status="READY"]');
    await expect(ready).toHaveCount(1, { timeout: 90_000 });
    await ready.getByRole("button", { name: "Integrity report" }).click();
    const report = page.getByTestId("integrity-report");
    await expect(report).toContainText("data/extractions.csv");
    await expect(report).toContainText("data/extractions.json");
    await page.keyboard.press("Escape");

    const downloading = page.waitForEvent("download");
    await ready.getByRole("button", { name: /^Download/ }).click();
    const file = testInfo.outputPath("package.zip");
    await (await downloading).saveAs(file);
    expect(fs.statSync(file).size).toBeGreaterThan(500);

    // 5. Verify it on the Batch operations page; a file that is not a package is refused.
    await page.goto(ws("C", "batches"));
    await expect(page.getByTestId("batch-row").filter({ hasText: name })).toBeVisible();
    await page.getByRole("button", { name: "Verify a package" }).click();
    await page.getByLabel("Package file").setInputFiles(file);
    await expect(page.getByTestId("verify-result")).toHaveAttribute("data-verdict", "VERIFIED", { timeout: 30_000 });
    await expect(page.getByTestId("verify-result")).toContainText("the manifest is the one FlowPilot issued");

    const notAPackage = testInfo.outputPath("notes.zip");
    fs.writeFileSync(notAPackage, "this is not a zip archive");
    await page.getByLabel("Package file").setInputFiles(notAPackage);
    await expect(page.getByTestId("verify-result")).toHaveAttribute("data-verdict", "INVALID");
    await expectHealthyPage(page);
  });

  test("the dispatch policy is a workspace setting the admin can change", async ({ page }) => {
    await page.goto(ws("C", "batches"));
    const policy = page.getByTestId("dispatch-policy");
    await expect(policy).toContainText("Dispatch policy");
    const threshold = policy.getByLabel("Straight-through threshold (percent)");
    await threshold.fill("95");
    await policy.getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByText("Dispatch policy saved.", { exact: false })).toBeVisible();
    await page.reload();
    await expect(page.getByTestId("dispatch-policy")).toContainText("95%");
    // Put the default back for the next run.
    await page.getByTestId("dispatch-policy").getByLabel("Straight-through threshold (percent)").fill("90");
    await page.getByTestId("dispatch-policy").getByRole("button", { name: "Save policy" }).click();
    await expect(page.getByText("Dispatch policy saved.", { exact: false })).toBeVisible();
  });
});

test.describe("Batch operations — viewer", () => {
  test.use({ user: "C.viewer" });

  test("a viewer reads batches and verifies packages but does not act", async ({ page }) => {
    await page.goto(ws("C", "batches"));
    await expect(page.getByRole("heading", { name: "Batch operations" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Verify a package" })).toBeVisible();
    await expect(page.getByRole("button", { name: "New batch" })).toHaveCount(0);
    await expect(page.getByTestId("dispatch-policy")).toContainText("Workspace admins can change the policy.");
    const first = page.getByTestId("batch-row").first();
    if (await first.count()) {
      await first.getByRole("link").click();
      await expect(page.getByRole("button", { name: "Dispatch", exact: true })).toHaveCount(0);
    }
    await expectHealthyPage(page);
  });
});

test.describe("Batch operations — Developer plan", () => {
  test.use({ user: "A.owner" });

  test("the page shows the lock and the server refuses", async ({ page, problems }) => {
    problems.allowHttp(/\/processing-batches/, [402], "the Developer plan does not include batch operations");
    await page.goto(ws("A", "batches"));
    await expect(page.locator("main")).toContainText("Batch operations is included on the Business and Enterprise plans.");
    const session = await loginAs("A.owner");
    const { workspaceId } = await resolveWorkspaceId(session, "e2e-devco", "main");
    const refused = await api(session, "GET", `/workspaces/${workspaceId}/processing-batches`);
    expect(refused.status).toBe(402);
    await settle(page, 200);
  });
});
