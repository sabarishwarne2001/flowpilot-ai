/**
 * THE FREE PLAN (campaign session 1): a deliberately small taste. Per month 25
 * documents, 50 OCR pages, 30 assistant messages; 10 MB per file, 10 pages per
 * document; one workspace; two seats. The screen where the allowance is spent says
 * where the organization stands; a refusal says why and what to do next.
 *
 * Tenant P ("FlowPilot Platform Ops") is the seeded Free organization. These tests
 * only exercise refusals, so no run spends that organization's monthly allowance.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org, ws } from "../support/env";
import { buildPdf } from "../support/sample-docs";

test.use({ user: "P.superadmin" });

test.describe("Free: uploads", () => {
  test("the upload screen shows the allowance and refuses a document the plan cannot take", async ({ page, problems }, testInfo) => {
    problems.allowHttp(/\/work-items$/, [402], "an 11-page document on Free is refused with the reason");
    await page.goto(ws("P"));
    const allowance = page.getByTestId("plan-allowance");
    await expect(allowance).toContainText("Free plan");
    await expect(allowance).toContainText(/of 25 documents this month/);
    await expect(allowance).toContainText("files up to 10 MB, 10 pages");

    // Eleven pages: the server refuses before anything is stored or charged.
    const long = testInfo.outputPath(`eleven-pages-${Date.now()}.pdf`);
    fs.writeFileSync(long, buildPdf(Array.from({ length: 11 }, (_, i) => [`Page ${i + 1}`])));
    await page.locator('main input[type="file"]').first().setInputFiles(long);
    await page.getByRole("button", { name: "Start Ingestion" }).click();
    await expect(page.getByTestId("upload-queue-item").first()).toContainText(
      "accepts documents of up to 10 pages",
      { timeout: 15_000 },
    );

    // Twelve megabytes: refused in the browser, from the same plan figure, before upload.
    const big = testInfo.outputPath(`twelve-mb-${Date.now()}.pdf`);
    fs.writeFileSync(big, Buffer.concat([buildPdf([["Big"]]), Buffer.alloc(12 * 1024 * 1024, 0x41)]));
    await page.locator('main input[type="file"]').first().setInputFiles(big);
    await expect(page.getByText(/larger than the 10 MB your Free plan accepts/)).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Free: workspaces", () => {
  test("a second workspace is refused with the way on, not a passing toast", async ({ page, problems }) => {
    problems.allowHttp(/\/organizations\/[^/]+\/workspaces$/, [402], "Free includes one workspace");
    await page.goto(org("P", "workspaces/new"));
    await page.getByLabel("Workspace name").fill("A second workspace");
    await page.getByRole("button", { name: "Create workspace" }).click();
    const card = page.getByRole("alert").filter({ hasText: "includes 1 workspace" });
    await expect(card).toBeVisible({ timeout: 15_000 });
    await expect(card.getByTestId("view-plans")).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Free: plan cards", () => {
  test("the cards list the counts a customer plans by, per seat on paid plans", async ({ page }) => {
    await page.goto(org("P", "billing"));
    const free = page.getByRole("list", { name: "Free allowances" });
    await expect(free).toContainText("25 documents uploaded / month");
    await expect(free).toContainText("30 assistant messages / month");
    await expect(free).toContainText("Files up to 10 MB");
    await expect(free).toContainText("2 seats (owner included)");
    const developer = page.getByRole("list", { name: "Developer allowances" });
    await expect(developer).toContainText("500 documents uploaded / month per seat");
    await expectHealthyPage(page);
  });
});

test.describe("Free: automations", () => {
  test("automations are not on Free: the page says so and offers no new rule", async ({ page }) => {
    await page.goto(ws("P", "automation"));
    const banner = page.getByTestId("plan-lock-banner");
    await expect(banner).toContainText("Automations isn't included in your plan", { timeout: 15_000 });
    await expect(page.getByRole("button", { name: "Create New Rule" })).toBeDisabled();
    await expectHealthyPage(page);
  });
});
