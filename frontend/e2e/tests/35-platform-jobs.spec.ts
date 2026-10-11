/**
 * THE BACKGROUND JOBS CONSOLE (campaign session 2, brief D: the dead-letter queue is visible).
 *
 * A job that failed every attempt was visible only as a failed document or a database row.
 * Platform staff now see waiting, running, retrying and dead jobs per type, read why each dead
 * job failed and for which organization, and send one round again once the cause is fixed.
 * Anyone else is not shown the console.
 *
 * The seed (backend/scripts/seed_e2e.py) puts one dead `document.extract` job back on every
 * run: its document is gone, so a retry runs and ends quietly.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, loginAs } from "../support/api";

const SEEDED_ERROR = "OCRUnavailableError: e2e seeded failure";

test.describe("Background jobs console: platform staff", () => {
  test.use({ user: "P.superadmin" });

  test("shows the queue, lists a dead job with its reason, and retries it", async ({ page }) => {
    await page.goto("/admin/jobs");
    const console_ = page.getByTestId("jobs-console");
    await expect(console_.getByRole("heading", { name: "Background jobs" })).toBeVisible({ timeout: 15_000 });
    await expect(console_.getByRole("cell", { name: "document.extract" })).toBeVisible();

    await page.getByRole("tab", { name: "Dead jobs" }).click();
    const row = page.getByTestId("dead-job-row").filter({ hasText: SEEDED_ERROR });
    await expect(row).toHaveCount(1, { timeout: 15_000 });
    await expect(row).toContainText("document.extract");

    await row.getByRole("button", { name: /^Retry document\.extract job/ }).click();
    await expect(page.getByText("The job was sent round again.")).toBeVisible();
    await expect(row).toHaveCount(0, { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});

test.describe("Background jobs console: everyone else", () => {
  test.use({ user: "C.owner" });

  test("an organization owner is not shown it and the API answers 404", async ({ page }) => {
    await page.goto("/admin/jobs");
    await expect(page.getByTestId("jobs-console")).toHaveCount(0);
    const owner = await loginAs("C.owner");
    expect((await api(owner, "GET", "/admin/jobs/summary")).status).toBe(404);
  });
});
