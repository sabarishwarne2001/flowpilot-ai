/**
 * JOURNEY 6 — a Business customer over every Free limit cancels (campaign session 4, §D; N-053).
 *
 * The organization holds more documents, workspaces and people than Free allows. Its trial was
 * over long ago, so when the subscription ends it becomes read-only rather than starting a new
 * trial: nothing is deleted or hidden, everyone can still read, and the owner is told exactly what
 * happened ("Your subscription has ended") and what to do (choose a plan). A member is told whom
 * to ask. Uploads are paused with the reason.
 *
 * The gateway's side (the cancellation) and the clock are played by `backend/scripts/e2e_stand_in.py`.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { addMember, documents, newCustomer, signInAs, standIn, uploadViaApi } from "../support/journey";

test.describe("Journey: Business over every Free limit cancels", () => {
  test.setTimeout(360_000);

  test("read-only, nothing deleted, the owner told what to do", async ({ page }) => {
    const customer = await newCustomer("lapse-journey", "Lapse Journey Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "4");

    // Over every Free limit: 26 documents (Free: 25), a second workspace (Free: 1), three people (Free: 2).
    for (let n = 0; n < 26; n += 1) {
      await uploadViaApi(customer, `lapse-${n}`);
    }
    const second = await api<{ id: string }>(customer.session, "POST", `/organizations/${customer.orgId}/workspaces`,
      { workspace_name: "Second desk" });
    expect(second.status, second.text).toBeLessThan(300);
    const member = await addMember(customer, "lapse-member");
    await addMember(customer, "lapse-viewer", "MEMBER", "VIEWER");

    // The trial ended long ago; the subscription is cancelled.
    standIn("trial-ends", customer.orgSlug);
    standIn("cancel", customer.orgSlug);

    // The owner: told what happened and what to do, in words that fit a former customer.
    await signInAs(page, customer.email);
    await page.goto(customer.ws());
    const notice = page.getByRole("alertdialog", { name: "Your subscription has ended" });
    await expect(notice).toBeVisible({ timeout: 15_000 });
    await expect(notice).toContainText("Nothing has been deleted");
    await notice.getByRole("button", { name: "Continue read-only" }).click();
    const strip = page.getByTestId("trial-ended-banner");
    await expect(strip).toContainText("Your subscription has ended");
    await expect(strip.getByRole("link", { name: "Choose a plan" })).toBeVisible();
    const dropzone = page.getByRole("button", { name: "Upload documents" });
    await expect(dropzone).toHaveAttribute("aria-disabled", "true");
    await expect(dropzone).toContainText("Uploads are paused: the subscription has ended");

    // Nothing was destroyed: every document, both workspaces and every person are still there.
    expect((await documents(customer)).length).toBe(26);
    await page.goto(customer.ws("work-items"));
    await expect(page.getByRole("table").getByText(/lapse-\d+/).first()).toBeVisible({ timeout: 15_000 });
    const workspaces = await api<{ organizations: Array<{ organization_slug: string; workspaces: unknown[] }> }>(
      customer.session, "GET", "/me/context");
    expect(workspaces.body.organizations.find((o) => o.organization_slug === customer.orgSlug)?.workspaces.length).toBe(2);
    await page.goto(customer.org("members"));
    await expect(page.locator("main")).toContainText(member.email);
    await expectHealthyPage(page);

    // A member is told whom to ask (the strip on every page; the full-page notice was already
    // dismissed in this browser tab, as it is once per browser session).
    await signInAs(page, member.email);
    await page.goto(customer.ws());
    const memberStrip = page.getByTestId("trial-ended-banner");
    await expect(memberStrip).toContainText("Your subscription has ended", { timeout: 15_000 });
    await expect(memberStrip).toContainText(`Ask ${customer.email} to subscribe to a plan`);
    await expect(memberStrip.getByRole("link", { name: "Choose a plan" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
