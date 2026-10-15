/**
 * HELM: "Explore Helm with a sample company" on a plan without Helm (brief §10.3).
 *
 * A new customer on its trial (Helm preview) switches to Helm and builds the sample company: the
 * worker writes Meridian Wholesale's nine months of documents and reads them with the platform's own
 * engines. The owner opens it, replays the nine months in the Time machine (the sample is free to
 * explore whole) and reads what Helm would have held, in its three honesty classes. The sample
 * workspace is labelled and deletable; deleting it takes its book away.
 */
import { test, expect, settle } from "../support/fixtures";
import { newCustomer, signInAs, type Customer } from "../support/journey";

let customer: Customer;

test.describe("Helm sample company — every plan can explore it", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(300_000);

  test.beforeAll(async () => {
    customer = await newCustomer("helmsample", "Sample Explorer Ltd");
  });

  test("build it, open it, replay nine months, then delete it", async ({ page }) => {
    await signInAs(page, customer.email);
    await page.goto(customer.ws());
    await settle(page);
    // On the Overview, Helm's preview card.
    const card = page.getByTestId("helm-overview-card");
    await expect(card).toContainText("Business and Enterprise");
    await expect(card.getByRole("link", { name: "Explore Helm" })).toBeVisible();
    await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ }).click();
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/helm/start$`));

    const sample = page.getByTestId("helm-sample");
    await expect(sample.getByRole("heading", { name: "Explore Helm with a sample company" })).toBeVisible();
    await sample.getByRole("button", { name: "Explore Helm with a sample company" }).click();
    await expect(sample.getByRole("status")).toContainText("Building Meridian Wholesale Pvt Ltd");
    const open = sample.getByRole("button", { name: "Open Meridian Wholesale Pvt Ltd" });
    await expect(open).toBeVisible({ timeout: 240_000 });
    await open.click();

    // Its cockpit, then the Time machine: the sample replays its nine months whole.
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Time machine" }).click();
    await expect(page.getByRole("heading", { name: "Time machine", level: 1 })).toBeVisible();
    await page.getByRole("combobox", { name: "Replay the last" }).selectOption("9");
    const queued = page.waitForResponse((r) => r.url().endsWith("/helm/backtests") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Replay with my mandates" }).click();
    expect((await queued).status()).toBe(201);
    const report = page.getByTestId("helm-backtest-report");
    await expect(report).toBeVisible({ timeout: 120_000 });
    await expect(report.getByTestId("helm-backtest-summary")).toContainText("Over the last 9 months");
    await expect(report.getByRole("button", { name: /Never pay above the agreed price/ })).toContainText("finding");
    await expect(report.getByRole("button", { name: /Never pay a duplicate/ })).toContainText("1 finding");

    // Delete it: the book goes with it.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Another workspace, or the sample company" }).click();
    await sample.getByRole("button", { name: "Delete the sample company" }).click();
    await sample.getByRole("button", { name: "Yes, delete it" }).click();
    await expect(sample.getByRole("button", { name: "Explore Helm with a sample company" })).toBeVisible();
  });
});
