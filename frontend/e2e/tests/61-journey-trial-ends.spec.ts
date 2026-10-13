/**
 * JOURNEY 2 — the one-week trial ends, then the customer subscribes (N-052, N-053; campaign
 * session 4, §D).
 *
 * A person signs up, verifies and creates an organization; the trial starts. Two days before
 * it ends they are emailed and see the days left. When it ends they are emailed again and the
 * organization is read-only: reading, opening a document, the members and billing pages still
 * work; uploads and the assistant are paused with the reason. They subscribe, and everything is
 * back at once.
 *
 * The clock and the payment gateway's side are played by `backend/scripts/e2e_stand_in.py`
 * (support/journey.ts); everything the customer sees is the real app, API and worker.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { waitForMail } from "../support/mail";
import { documents, invoicePdf, newCustomer, runJob, signInAs, standIn, uploadInBrowser } from "../support/journey";

test.describe("Journey: the trial ends, then the customer subscribes", () => {
  test.setTimeout(300_000);

  test("trial → reminder → read-only → subscribe → everything back", async ({ page }) => {
    const customer = await newCustomer("trial-journey", "Trial Journey Ltd");
    await signInAs(page, customer.email);

    // During the trial: a document goes in.
    await uploadInBrowser(page, customer, invoicePdf("trial-first"));
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(1);

    // Two days left: the reminder email, and the banner with the way to the plans.
    let since = Date.now() - 1_000;
    standIn("trial-ending", customer.orgSlug);
    runJob("trial.reminders");
    await waitForMail(customer.email, /trial ends on/i, since, 90_000);
    await page.goto(customer.ws());
    await expect(page.getByTestId("trial-banner")).toContainText("Your free trial ends in 2 days", { timeout: 15_000 });

    // The trial ends: an email, then a full-page notice; reading stays open.
    since = Date.now() - 1_000;
    standIn("trial-ends", customer.orgSlug);
    runJob("trial.reminders");
    await waitForMail(customer.email, /trial has ended/i, since, 90_000);
    await page.goto(customer.ws());
    const notice = page.getByRole("alertdialog", { name: "Your free trial has ended" });
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText("Nothing has been deleted");
    await notice.getByRole("button", { name: "Continue read-only" }).click();

    await page.goto(customer.ws("work-items"));
    const row = page.getByRole("table").getByText(/trial-first/);
    await expect(row).toBeVisible({ timeout: 15_000 });
    await page.getByRole("row", { name: /trial-first/ }).getByRole("link", { name: "View Details" }).click();
    await expect(page.locator("main")).toContainText(/INV-|Acme|invoice/i, { timeout: 15_000 });
    await page.goto(customer.org("members"));
    await expect(page.locator("main")).toContainText(customer.email);
    await page.goto(customer.org("billing"));
    await expect(page.getByRole("list", { name: "Developer allowances" })).toBeVisible();
    await expectHealthyPage(page);

    // Uploads and the assistant are paused, and say why.
    await page.goto(customer.ws());
    const dropzone = page.getByRole("button", { name: "Upload documents" });
    await expect(dropzone).toHaveAttribute("aria-disabled", "true");
    await expect(dropzone).toContainText("Uploads are paused: the free trial has ended");
    const assistant = await api<{ code?: string; details?: { remedy?: string } }>(
      customer.session, "POST", `/workspaces/${customer.wsId}/assistant/conversations`, { title: "After the trial" },
    );
    expect(assistant.status, assistant.text).toBe(402);
    expect(assistant.text).toContain("TRIAL_ENDED");

    // They subscribe (the gateway confirms the payment): everything is back at once.
    standIn("subscribe", customer.orgSlug, "--plan", "developer", "--seats", "1");
    await page.goto(customer.ws());
    await expect(page.getByTestId("trial-ended-banner")).toHaveCount(0, { timeout: 15_000 });
    await expect(page.getByRole("alertdialog", { name: "Your free trial has ended" })).toHaveCount(0);
    await uploadInBrowser(page, customer, invoicePdf("trial-after"));
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(2);
    const resumed = await api(customer.session, "POST", `/workspaces/${customer.wsId}/assistant/conversations`,
      { title: "Subscribed" });
    expect(resumed.status, resumed.text).toBeLessThan(300);
    await page.goto(customer.org("billing"));
    await expect(page.locator("main")).toContainText(/Developer/);
    await expectHealthyPage(page);
  });
});
