/**
 * HELM: the guided tour on the sample company (owner addition C), and its supplier statement
 * reconciliation (owner addition B), in the browser.
 *
 * A Business customer builds the sample company and takes the tour from Start: the instruments, the
 * flight path, the plan and its Guard, a certificate, the Time machine, then "Your turn". Each step
 * outlines the part of the page it explains, survives a reload (the step is in the address), and
 * Escape ends it. Back on the cockpit, the plan proposes reconciling Sahyadri's statement of account:
 * its differences are listed with their evidence, and approving it drafts the email to copy (the
 * sample company never sends mail).
 */
import { test, expect, settle } from "../support/fixtures";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

let customer: Customer;

test.describe("Helm tour and supplier reconciliation on the sample company", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(300_000);

  test.beforeAll(async () => {
    customer = await newCustomer("helmtour", "Tour Taker Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "3");
  });

  test("the tour walks the sample company, and its reconciliation drafts an email", async ({ page }) => {
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/start`);
    await settle(page);
    const sample = page.getByTestId("helm-sample");
    await sample.getByRole("button", { name: "Explore Helm with a sample company" }).click();
    const tourButton = sample.getByRole("button", { name: "Take the guided tour" });
    await expect(tourButton).toBeVisible({ timeout: 240_000 });
    await tourButton.click();

    const tour = page.getByRole("dialog", { name: "The instruments" });
    await expect(tour).toBeVisible();
    await expect(tour).toContainText("step 1 of 6");
    await expect(page.locator('[data-tour="instruments"]')).toHaveClass(/helm-tour-target/);
    await expect(page.getByRole("heading", { name: "The instruments", level: 2 })).toBeFocused();

    await tour.getByRole("link", { name: "Next: The flight path" }).click();
    await expect(page.getByRole("dialog", { name: "The flight path" })).toBeVisible();
    await expect(page.locator('[data-tour="flight"]')).toHaveClass(/helm-tour-target/);
    await expect(page.locator('[data-tour="instruments"]')).not.toHaveClass(/helm-tour-target/);

    await page.getByRole("link", { name: "Next: The plan, and the Guard" }).click();
    await expect(page.locator('[data-tour="plan"]')).toHaveClass(/helm-tour-target/);

    await page.getByRole("link", { name: "Next: The Guard's certificate" }).click();
    await expect(page).toHaveURL(/\/helm\/certificates\?.*tour=3/);
    await expect(page.getByRole("heading", { name: "Certificates", level: 1 })).toBeVisible();
    // The step is in the address: a reload keeps the tour where it was.
    await page.reload();
    await expect(page.getByRole("dialog", { name: "The Guard's certificate" })).toBeVisible();

    await page.getByRole("link", { name: "Next: The Time machine" }).click();
    await expect(page.getByRole("heading", { name: "Time machine", level: 1 })).toBeVisible();
    await expect(page.locator('[data-tour="time-machine"]')).toHaveClass(/helm-tour-target/);

    await page.getByRole("link", { name: "Next: Your turn" }).click();
    const last = page.getByRole("dialog", { name: "Your turn" });
    await expect(last.getByRole("link", { name: "Set up Helm on my documents" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expect(page.getByTestId("helm-tour")).toHaveCount(0);
    await expect(page).not.toHaveURL(/tour=/);

    // The plan proposes reconciling Sahyadri's statement of account, with the differences in words.
    const reconcile = page.getByTestId("helm-action").filter({ hasText: "Reconcile Sahyadri Packaging Industries" });
    await expect(reconcile).toBeVisible();
    await expect(reconcile).toContainText("Reconcile");
    await reconcile.getByRole("button", { name: "Why, and what the Guard checked" }).click();
    const differences = reconcile.getByTestId("helm-differences");
    await expect(differences).toContainText("SPI-9917");
    await expect(differences).toContainText("Helm has no such invoice from them");
    await expect(differences).toContainText("their statement does not show it");
    await expect(differences.getByRole("listitem")).toHaveCount(4);

    await reconcile.getByRole("button", { name: /^Approve: Reconcile/ }).click();
    const email = page.getByTestId("helm-draft-email").filter({ hasText: "Statement reconciliation" });
    await expect(email).toBeVisible({ timeout: 60_000 });
    await expect(email).toContainText("The drafted email");
    await expect(email).toContainText("which we have not received");
    await expect(email.getByRole("button", { name: /^Copy the email/ })).toBeVisible();
  });
});
