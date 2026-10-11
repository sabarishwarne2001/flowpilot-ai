/**
 * THE ONE-WEEK FREE TRIAL (owner decision N-052, built in campaign session 2).
 *
 * Free is a trial of seven days per owner account. During the last three days every
 * member sees how long is left; an owner is offered the plans. When it ends without a
 * subscription the organization is read-only: an owner is shown a full-page "Your free
 * trial has ended" with the way to the plans, everyone else is told whom to ask, and
 * uploads are refused with the reason. Reading stays open.
 *
 * Seeded tenants: S ("Soon Ending Trial Co", two days left) and T ("Trial Ended Co",
 * ended a day ago). P (the Free tenant of the other specs) has six days left and sees no
 * banner.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org, ws } from "../support/env";

test.describe("Trial: the last days", () => {
  test.use({ user: "S.owner" });

  test("the owner sees the days left and reaches the plans from the banner", async ({ page }) => {
    await page.goto(ws("S"));
    const banner = page.getByTestId("trial-banner");
    await expect(banner).toContainText("Your free trial ends in 2 days", { timeout: 15_000 });
    await banner.getByRole("link", { name: "Choose a plan" }).click();
    await expect(page).toHaveURL(new RegExp(`${org("S", "billing")}$`));
    await expect(page.getByRole("list", { name: "Developer allowances" })).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Trial: more than three days left", () => {
  test.use({ user: "P.superadmin" });

  test("no banner yet", async ({ page }) => {
    await page.goto(ws("P"));
    await expect(page.getByTestId("plan-allowance")).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId("trial-banner")).toHaveCount(0);
    await expect(page.getByTestId("trial-ended-banner")).toHaveCount(0);
  });
});

test.describe("Trial: ended, as the owner", () => {
  test.use({ user: "T.owner" });

  test("a full-page notice leads to the plans; reading stays open afterwards", async ({ page }) => {
    await page.goto(ws("T"));
    const dialog = page.getByRole("alertdialog", { name: "Your free trial has ended" });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await expect(dialog).toContainText("Nothing has been deleted");
    await dialog.getByRole("button", { name: "Choose a plan" }).click();
    await expect(page).toHaveURL(new RegExp(`${org("T", "billing")}$`));
    await expect(page.getByRole("list", { name: "Business allowances" })).toBeVisible();
    // On the plans page there is nothing more to push: no dialog, no button back to itself.
    await expect(page.getByRole("alertdialog", { name: "Your free trial has ended" })).toHaveCount(0);
    await expect(page.getByTestId("trial-ended-banner")).toBeVisible();

    // Dismissed once per browser session; the strip stays on every page.
    await page.goto(ws("T", "work-items"));
    await expect(page.getByTestId("trial-ended-banner")).toContainText("You can still read, search and export");
    await expect(page.getByRole("alertdialog", { name: "Your free trial has ended" })).toHaveCount(0);
    await expectHealthyPage(page);
  });

  test("continuing read-only closes the notice and the workspace still reads", async ({ page }) => {
    await page.goto(ws("T"));
    const dialog = page.getByRole("alertdialog", { name: "Your free trial has ended" });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await dialog.getByRole("button", { name: "Continue read-only" }).click();
    await expect(dialog).toHaveCount(0);
    await expect(page.getByTestId("trial-ended-banner").getByRole("link", { name: "Choose a plan" })).toBeVisible();
    await expectHealthyPage(page);
  });
});

test.describe("Trial: ended, as a member", () => {
  test.use({ user: "T.member" });

  test("a member is told whom to ask, and the upload area says uploads are paused", async ({ page }) => {
    await page.goto(ws("T"));
    const dialog = page.getByRole("alertdialog", { name: "Your free trial has ended" });
    await expect(dialog).toBeVisible({ timeout: 15_000 });
    await expect(dialog).toContainText("Ask Tess Owner to subscribe to a plan.");
    await expect(dialog.getByRole("button", { name: "Choose a plan" })).toHaveCount(0);
    await dialog.getByRole("button", { name: "Continue read-only" }).click();
    await expect(page.getByTestId("trial-ended-banner")).toContainText("Ask Tess Owner to subscribe to a plan.");

    // Not a dead control: the drop area no longer invites an upload the server would refuse.
    const dropzone = page.getByRole("button", { name: "Upload documents" });
    await expect(dropzone).toHaveAttribute("aria-disabled", "true");
    await expect(dropzone).toContainText("Uploads are paused: the free trial has ended");
    await expectHealthyPage(page);
  });
});
