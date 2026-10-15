/**
 * HELM: from a sentence to a mandate, in the browser (brief §5.3, build step 5).
 *
 * The owner of a new Business customer writes "Keep at least ₹5 lakh in the bank". The model
 * stand-in reads it as "Keep cash above a floor" with a floor of 5 lakh; Helm checks it and reads it
 * back with the amount as a chip. The owner changes the chip to 7.5 lakh and adds it: it starts in
 * Propose mode. Editing it sets Act mode with an act cap, saved as version 2. A sentence Helm cannot
 * map offers the gallery; a sentence with no amount shows the example as an example and cannot be
 * saved until the owner sets it; a gallery mandate whose value has no default ("Every invoice above
 * an amount has a matching PO") asks for the person's own number before it can be added.
 */
import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

const NEEDS_MODEL = "needs the model stand-in: run with E2E_LLM=1 (support/llm-mock.mjs)";

let customer: Customer;
let bookId: string;

test.describe("Helm mandates — a sentence, the readback, the chips, the modes", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(180_000);

  test.beforeAll(async () => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    customer = await newCustomer("helmmd", "Helm Mandates Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "3");
    const book = await api<{ id: string }>(customer.session, "POST", `/organizations/${customer.orgId}/helm/books`, {
      workspace_id: customer.wsId,
    });
    expect(book.status, book.text.slice(0, 300)).toBe(201);
    bookId = book.body.id;
  });

  test("a sentence becomes a checked mandate the owner adjusts, adds and then lets act within a cap", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/mandates?book=${bookId}`);
    await settle(page);
    await expect(page.getByRole("heading", { name: "Mandates", level: 1 })).toBeVisible();

    // The sentence, read by the model stand-in and checked by Helm.
    const field = page.getByRole("textbox", { name: "What should always be true in your business?" });
    await field.fill("Keep at least ₹5 lakh in the bank");
    const drafted = page.waitForResponse((r) => r.url().endsWith("/mandates/draft") && r.request().method() === "POST");
    await page.getByRole("button", { name: "Draft it" }).click();
    expect((await drafted).status()).toBe(200);
    const draft = page.getByTestId("helm-draft");
    await expect(draft.getByRole("status")).toHaveText("Helm read it as Keep cash above a floor.");
    const editor = draft.getByTestId("helm-mandate-editor");
    await expect(editor.getByTestId("helm-readback")).toContainText("Keep INR cash above ₹5 lakh");
    await expect(editor.getByText("It starts in Propose mode: Helm suggests, a person decides.")).toBeVisible();
    await expect(draft.getByRole("button", { name: "Pay every supplier on time" })).toBeVisible(); // did you mean

    // A chip is a value the owner can change in place; Helm checks it again.
    await editor.getByRole("button", { name: "Change ₹5 lakh" }).click();
    const value = editor.getByRole("textbox", { name: "New value for ₹5 lakh" });
    await value.fill("seven");
    await editor.getByRole("button", { name: "Set" }).click();
    await expect(editor.getByRole("alert")).toHaveText("Write an amount, for example 500000 or 2,50,000.");
    await value.fill("7,50,000");
    const checked = page.waitForResponse((r) => r.url().endsWith("/mandates/preview"));
    await editor.getByRole("button", { name: "Set" }).click();
    expect((await checked).status()).toBe(200);
    await expect(editor.getByTestId("helm-readback")).toContainText("Keep INR cash above ₹7.5 lakh");

    const created = page.waitForResponse((r) => /\/mandates$/.test(r.url()) && r.request().method() === "POST");
    await editor.getByRole("button", { name: "Add in Propose mode" }).click();
    expect((await created).status()).toBe(201);
    const card = page.getByTestId("helm-mandate").filter({ hasText: "Keep cash above a floor" });
    await expect(card).toContainText("Active");
    await expect(card).toContainText("Propose");
    await expect(card).toContainText("₹7.5 lakh");
    await expect(page.getByTestId("helm-draft")).toHaveCount(0);

    // Editing writes a new version: Act mode, with a cap on what Helm may run alone.
    await card.getByRole("button", { name: "Edit Keep cash above a floor" }).click();
    const edit = page.getByTestId("helm-mandate-editor");
    await expect(edit.getByRole("heading", { name: "Edit Keep cash above a floor" })).toBeFocused();
    await edit.getByRole("radio", { name: "Act" }).check();
    await expect(edit.getByText("Helm runs actions on its own up to the act cap; anything above it asks.")).toBeVisible();
    await edit.getByRole("textbox", { name: "Act cap" }).fill("25000");
    const saved = page.waitForResponse((r) => /\/mandates\/[^/]+$/.test(r.url()) && r.request().method() === "PATCH");
    await edit.getByRole("button", { name: "Save as version 2" }).click();
    expect((await saved).status()).toBe(200);
    const actCard = page.getByTestId("helm-mandate").filter({ hasText: "Keep cash above a floor" });
    await expect(actCard).toContainText("Act");
    await expect(actCard).toContainText("version 2");
  });

  test("a drafted mandate is replayed in the Time machine before it is saved", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/mandates?book=${bookId}`);
    await settle(page);
    await page.getByRole("textbox", { name: "What should always be true in your business?" }).fill("Never pay the same invoice twice");
    await page.getByRole("button", { name: "Draft it" }).click();
    const editor = page.getByTestId("helm-draft").getByTestId("helm-mandate-editor");
    await expect(editor.getByTestId("helm-readback")).toContainText("Never pay a duplicate invoice");
    const queued = page.waitForResponse((r) => r.url().endsWith("/helm/backtests") && r.request().method() === "POST");
    await editor.getByRole("button", { name: "See what it would have done over the last 6 months" }).click();
    expect((await queued).status()).toBe(201);

    await expect(page).toHaveURL(/\/helm\/time-machine\?book=[^&]+&backtest=/);
    await expect(page.getByRole("heading", { name: "Time machine", level: 1 })).toBeVisible();
    const report = page.getByTestId("helm-backtest-report");
    await expect(report).toBeVisible({ timeout: 60_000 });
    await expect(report.getByTestId("helm-backtest-summary")).not.toBeEmpty();
    for (const honesty of ["Confirmed", "Likely", "Potential"]) {
      await expect(report.getByText(honesty, { exact: true }).first()).toBeVisible();
    }
    await expect(report.getByRole("button", { name: /Never pay a duplicate/ })).toContainText("nothing in this period");
    // Nothing was saved: the replay ran on a draft.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Mandates" }).click();
    await expect(page.getByTestId("helm-mandate").filter({ hasText: "Never pay a duplicate" })).toHaveCount(0);
  });

  test("Helm offers the gallery when it cannot map a sentence, and never treats an example as a fact", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm/mandates?book=${bookId}`);
    await settle(page);
    const field = page.getByRole("textbox", { name: "What should always be true in your business?" });

    // Nothing in the library does this: the gallery, with nothing lost.
    await field.fill("Make the office coffee stronger");
    await page.getByRole("button", { name: "Draft it" }).click();
    await expect(page.getByTestId("helm-draft").getByRole("status")).toHaveText(
      "None of Helm's mandates does that yet. These are the closest ones in the gallery.",
    );
    await expect(page.getByTestId("helm-draft")).toContainText("Pick one from the library below.");

    // No amount in the sentence: the example is shown as an example and cannot be saved.
    await field.fill("Make sure our cash never runs low");
    await page.getByRole("button", { name: "Draft it" }).click();
    const editor = page.getByTestId("helm-draft").getByTestId("helm-mandate-editor");
    await expect(editor.getByTestId("helm-problems")).toContainText("Set the floor: the value shown is an example, not yours.");
    await expect(editor.getByRole("button", { name: "Add in Propose mode" })).toBeDisabled();
    await editor.getByRole("button", { name: "Cancel" }).click();

    // A gallery mandate whose value has no default asks for the person's own number.
    const template = page.getByTestId("helm-template").filter({ hasText: "Every invoice above an amount has a matching PO" });
    await template.getByRole("button", { name: "Add Every invoice above an amount has a matching PO" }).click();
    const fromGallery = page.getByTestId("helm-mandate-editor").filter({ hasText: "Every invoice above an amount has a matching PO" });
    await expect(fromGallery.getByTestId("helm-problems")).toHaveText("Set the threshold: the value shown is an example, not yours.");
    const add = fromGallery.getByRole("button", { name: "Add in Propose mode" });
    await expect(add).toBeDisabled();
    await fromGallery.getByRole("button", { name: "Change ₹50,000" }).click();
    await fromGallery.getByRole("textbox", { name: "New value for ₹50,000" }).fill("100000");
    await fromGallery.getByRole("button", { name: "Set" }).click();
    await expect(fromGallery.getByTestId("helm-readback")).toContainText("₹1 lakh");
    await expect(add).toBeEnabled();
    await add.click();
    await expect(page.getByTestId("helm-mandate").filter({ hasText: "Every invoice above an amount has a matching PO" })).toContainText(
      "₹1 lakh",
    );
  });
});
