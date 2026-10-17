/**
 * FIRST RUN (campaign session 4, §A.4): a new customer reaches a useful result in minutes.
 *
 * A new organization's dashboard shows "Get started with Servabo": add a document, see what was
 * read from it, ask the assistant, invite a colleague. With no document at hand the owner tries
 * the built-in sample invoice (an ordinary upload made by the server); each step ticks itself from
 * the workspace's real state, the read document opens from the card, and the card can be hidden.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { newCustomer, signInAs } from "../support/journey";

test.describe("First run", () => {
  test.setTimeout(240_000);

  test("a new customer tries the sample invoice from the getting-started card", async ({ page }) => {
    const customer = await newCustomer("first-run", "First Run Ltd");
    await signInAs(page, customer.email);
    await page.goto(customer.ws(""));

    const card = page.getByRole("region", { name: "Get started with Servabo" });
    await expect(card).toBeVisible({ timeout: 30_000 });
    await expect(card).toContainText("0 of 4 done");
    const steps = card.getByRole("list", { name: "Getting started steps" });
    await expect(steps.getByRole("listitem")).toHaveCount(4);

    await card.getByRole("button", { name: "Try a sample invoice" }).click();
    await expect(page.getByText(/Sample invoice added/)).toBeVisible({ timeout: 15_000 });
    await expect(steps.getByRole("listitem").first()).toContainText("(done)", { timeout: 30_000 });

    // Read by the worker like any upload; the step then offers the document itself.
    const open = card.getByRole("link", { name: "Open sample-invoice-northwind.pdf" });
    await expect(open).toBeVisible({ timeout: 180_000 });
    await expect(card).toContainText("2 of 4 done");
    await open.click();
    await expect(page).toHaveURL(/\/work-items\/[0-9a-f-]{36}/);
    await expect(page.locator("main")).toContainText("sample-invoice-northwind.pdf", { timeout: 30_000 });
    await expectHealthyPage(page);

    // Hidden by the person, it stays hidden in this browser.
    await page.goto(customer.ws(""));
    await page.getByRole("button", { name: "Hide getting started" }).click();
    await expect(card).toHaveCount(0);
    await page.reload();
    await expect(page.locator("main")).toContainText(/Overview|Documents/, { timeout: 30_000 });
    await expect(page.getByRole("region", { name: "Get started with Servabo" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
