/**
 * THE MATCHING QUEUE HOLDS ONE CASE PER INVOICE (campaign session 2, F-264, N-055).
 *
 * The same invoice often arrives twice (emailed then posted, rescanned, re-sent by a supplier
 * chasing payment). A later copy (same supplier and invoice number, or the same file) opens no
 * case of its own: the first copy's case lists it, so the reviewer sees the duplicate-payment
 * risk where they approve. Tenant C's seeded invoice INV-E2E-1001 gets a rescanned copy here.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { TENANTS, ws } from "../support/env";
import { listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "../support/api";
import { INVOICE_1001_RESCAN, buildPdf } from "../support/sample-docs";

test.use({ user: "C.owner" });

test("a rescanned copy of an invoice is flagged on its case, not queued as a second case", async ({ page }, testInfo) => {
  test.setTimeout(150_000);
  const session = await loginAs("C.owner");
  const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
  const name = `invoice-INV-E2E-1001-rescan-${Date.now()}.pdf`;
  const file = testInfo.outputPath(name);
  fs.writeFileSync(file, buildPdf(INVOICE_1001_RESCAN));
  const upload = await uploadFile(session, workspaceId, file);
  expect(upload.status, upload.text).toBe(201);
  // Processing, then the arrival scoring, finish on the worker.
  await expect
    .poll(async () => {
      const items = await listWorkItems(session, workspaceId, `search=${encodeURIComponent(name)}&limit=10`);
      return String(items[0]?.status ?? "");
    }, { timeout: 90_000 })
    .toMatch(/COMPLETED|NEEDS_REVIEW/);

  await page.goto(ws("C", "procurement"));
  const rows = page.locator("main tbody tr").filter({ hasText: "INV-E2E-1001" });
  await expect(rows.first()).toBeVisible({ timeout: 30_000 });
  await expect(rows).toHaveCount(1);
  await expect(rows.first().getByTestId("duplicate-copies-badge")).toBeVisible({ timeout: 30_000 });

  await rows.first().click();
  const flag = page.getByTestId("duplicate-copies");
  await expect(flag).toContainText("This invoice arrived");
  await expect(flag).toContainText(name);
  await expectHealthyPage(page);
});
