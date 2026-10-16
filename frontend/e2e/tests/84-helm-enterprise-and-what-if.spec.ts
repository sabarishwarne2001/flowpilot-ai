/**
 * HELM: box 7 in the browser, on the sample company of an Enterprise customer.
 *
 * What if…: beside the flight path, "customers pay 30 days later" is planned beside today's plan and
 * answered in words; nothing is saved, and Clear goes back. A value of the wrong kind is refused in
 * words before anything is sent. Custom condition: built in the mandate editor from one row (amount
 * above ₹5 lakh, hold the invoice) and added in Propose mode. Approval chain: a tier the
 * organization's people could never meet (two approvals, one person) is refused in words, and
 * removing the tier leaves one approval for every payment.
 */
import { test, expect, settle } from "../support/fixtures";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

let customer: Customer;

test.describe("Helm's Enterprise controls and what-if on the sample company", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(300_000);

  test.beforeAll(async () => {
    customer = await newCustomer("helment", "Enterprise Helm Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "5");
  });

  test("what-if, a custom condition and the approval chain", async ({ page, problems }) => {
    // The chain one person can never meet is refused (422), on purpose.
    problems.allowHttp(/\/helm\/books\/[^/]+\/settings$/, [422], "a chain the organization cannot meet is refused");
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/start`);
    await settle(page);
    const sample = page.getByTestId("helm-sample");
    await sample.getByRole("button", { name: "Explore Helm with a sample company" }).click();
    const open = sample.getByRole("button", { name: "Open Meridian Wholesale Pvt Ltd" });
    await expect(open).toBeVisible({ timeout: 240_000 });
    await open.click();
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();

    // What if… customers pay 30 days later.
    const whatIf = page.getByTestId("helm-what-if");
    await expect(whatIf.getByRole("heading", { name: "What if…" })).toBeVisible();
    const delay = whatIf.getByRole("textbox", { name: /Customers pay later by/ });
    await delay.fill("soon");
    await expect(whatIf.getByRole("button", { name: "Run" })).toBeDisabled();
    await delay.fill("30");
    await whatIf.getByRole("button", { name: "Run" }).click();
    const result = page.getByTestId("helm-what-if-result");
    await expect(result).toBeVisible({ timeout: 60_000 });
    await expect(result).toContainText("30 days");
    await whatIf.getByRole("button", { name: "Clear" }).click();
    await expect(page.getByTestId("helm-what-if-result")).toHaveCount(0);

    // A custom condition, built row by row.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Mandates" }).click();
    await expect(page.getByRole("heading", { name: "Mandates", level: 1 })).toBeVisible();
    await page.getByRole("button", { name: "Add Custom condition" }).click();
    const conditions = page.getByTestId("helm-conditions-editor");
    await expect(conditions).toBeVisible();
    await conditions.getByRole("textbox", { name: "Name" }).fill("Large invoices");
    await conditions.getByRole("textbox", { name: "Name" }).press("Enter");
    await conditions.getByRole("combobox", { name: "Fact for condition 1" }).selectOption({ label: "Amount" });
    await conditions.getByRole("combobox", { name: "Comparison for condition 1" }).selectOption({ label: "is above" });
    await conditions.getByRole("textbox", { name: "Amount for condition 1" }).fill("500000");
    await conditions.getByRole("textbox", { name: "Amount for condition 1" }).blur();
    await conditions.getByRole("radio", { name: "Hold the invoice" }).check();
    const editor = page.getByTestId("helm-mandate-editor");
    await expect(editor.getByTestId("helm-readback")).toContainText("5 lakh");
    await editor.getByRole("button", { name: "Add in Propose mode" }).click();
    await expect(page.getByTestId("helm-mandate").filter({ hasText: "Large invoices" })).toBeVisible();

    // The approval chain: one person cannot be two approvals.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Settings" }).click();
    const chain = page.getByTestId("helm-approval-chain");
    await expect(chain).toContainText("No chain: one approval is enough for every payment.");
    await chain.getByRole("button", { name: "Add a tier" }).click();
    const tier = chain.getByTestId("helm-chain-tier");
    await tier.getByRole("textbox", { name: "Above" }).fill("100000");
    await chain.getByRole("button", { name: "Save" }).click();
    await expect(chain.getByRole("alert")).toContainText("could never be approved");
    await tier.getByRole("button", { name: "Remove tier 1" }).click();
    await expect(chain).toContainText("No chain: one approval is enough for every payment.");
  });
});
