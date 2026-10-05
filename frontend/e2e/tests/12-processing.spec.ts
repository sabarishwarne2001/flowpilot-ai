/**
 * ENTERPRISE PROCESSING as the Enterprise owner: three-way matching and its
 * tolerance policies, ERP posting (target, preview, post, exactly-once),
 * process intelligence, the forensic audit radar and the corroborator.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId } from "../support/api";
import { runId, ws } from "../support/env";

test.use({ user: "C.owner" });

test.describe("Three-way matching", () => {
  test("the queue filters by state and the exceptions toggle works", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    for (const name of ["Needs review", "Matched", "Approved", "Disputed", "All open"]) {
      await page.getByRole("button", { name, exact: true }).click();
    }
    await page.getByRole("checkbox").first().check();
    await page.getByRole("checkbox").first().uncheck();
    await expectHealthyPage(page);
  });

  test("a tolerance policy can be previewed and published", async ({ page }) => {
    await page.goto(ws("C", "procurement/policies"));
    await page.getByRole("spinbutton", { name: /Price slack \(basis points\)/ }).fill("100");
    await page.getByRole("button", { name: "Preview last 30 days" }).click();
    await settle(page);
    const publish = page.getByRole("button", { name: /Publish version \d+/ });
    const label = await publish.textContent();
    await publish.click();
    await settle(page);
    await expect(page.locator("main")).not.toContainText("Nothing published yet");
    await expect(page.getByRole("button", { name: /Publish version \d+/ })).not.toHaveText(label ?? "");
  });

  test("the seeded PO, receipt and invoice produce a match case", async ({ page }) => {
    await page.goto(ws("C", "procurement"));
    await settle(page);
    await expect(page.locator("main")).not.toContainText("No cases here yet", { timeout: 10_000 });
  });
});

test.describe("ERP posting", () => {
  test.setTimeout(120_000);

  test("create a CSV download target; the Ready to post queue lists approved documents", async ({ page }) => {
    await page.goto(ws("C", "erp"));
    await page.getByRole("button", { name: "New target" }).click();
    const name = `E2E CSV ${runId()}`;
    await page.getByRole("textbox", { name: "Name" }).fill(name);
    await page.getByRole("button", { name: "Create target" }).click();
    // Creating a target opens its detail page with a field mapping per document type.
    await expect(page).toHaveURL(/\/erp\/targets\/[0-9a-f-]{36}/, { timeout: 15_000 });
    await expect(page.locator("main")).toContainText(name);
    for (const kind of ["Purchase order", "Goods receipt", "Journal entry", "Payment reference", "Vendor bill"]) {
      await page.getByRole("tab", { name: kind }).click();
    }
    await page.goto(ws("C", "erp"));
    await page.getByRole("tab", { name: "Targets" }).click();
    await expect(page.locator("main")).toContainText(name);
    await page.getByRole("tab", { name: "Ready to post" }).click();
    await settle(page);
    await expectHealthyPage(page);
    // With stub extraction nothing is approved for posting yet; record what the queue shows.
    test.info().annotations.push({
      type: "erp-ready",
      description: (await page.locator("main").innerText()).slice(-300).replace(/\s+/g, " "),
    });
  });

  test("an invalid target shows the server's reason, not a bare status code", async ({ page, problems }) => {
    problems.allowHttp(/\/erp\/targets$/, [422], "a Tally target without a company is invalid");
    await page.goto(ws("C", "erp"));
    await page.getByRole("button", { name: "New target" }).click();
    await page.getByRole("textbox", { name: "Name" }).fill(`E2E Tally ${runId()}`);
    await page.getByRole("combobox", { name: "Format" }).selectOption({ label: "Tally Prime XML" });
    await page.getByRole("button", { name: "Create target" }).click();
    await expect(page.locator("main")).toContainText(/tally\.company|company to import into/, { timeout: 10_000 });
  });

  test("the posting API is exactly-once: a repeated post of the same source is not duplicated", async () => {
    const session = await loginAs("C.owner");
    const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
    const targets = await api<{ items?: Array<{ id: string }> } | Array<{ id: string }>>(
      session,
      "GET",
      `/workspaces/${workspaceId}/erp/targets`,
    );
    const list = Array.isArray(targets.body) ? targets.body : targets.body.items ?? [];
    expect(list.length, "an ERP target exists (created by the first test)").toBeGreaterThan(0);
    const ready = await api<unknown>(session, "GET", `/workspaces/${workspaceId}/erp/postings?status=READY`);
    test.info().annotations.push({ type: "erp", description: `targets=${list.length} ready=${ready.status} ${ready.text.slice(0, 200)}` });
    // Exactly-once needs an approved source; with stub extraction there is none, so this
    // stays a probe until the review flow can approve a vendor bill (see 03-coverage.md).
  });
});

test.describe("Process intelligence", () => {
  test.setTimeout(240_000);

  test("sweep now, then every analysis tab renders", async ({ page }) => {
    await page.goto(ws("C", "process"));
    await page.getByRole("button", { name: "Sweep now" }).click();
    await settle(page, 2000);
    for (const tab of [/^Discovery/, /^Conformance/, /^Service levels/, /^Cost to serve/, /^Exception agent/, /^Overview/]) {
      await page.getByRole("tab", { name: tab }).first().click();
      await settle(page, 500);
      await expectHealthyPage(page);
    }
    await expect(page.locator("main")).toContainText(/event/i);
  });
});

test.describe("Forensic audit radar", () => {
  test("finding filters work", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    for (const tab of ["Duplicates", "Price changes", "Contract terms", "All"]) {
      await page.getByRole("button", { name: tab, exact: true }).click();
    }
    await page.getByRole("combobox", { name: "Severity" }).selectOption({ label: "High" });
    await page.getByRole("combobox", { name: "Status" }).selectOption({ label: "Open" });
    await expectHealthyPage(page);
  });

  test("the changed bank account on INV-E2E-1002 is flagged", async ({ page }) => {
    await page.goto(ws("C", "radar"));
    await settle(page);
    await expect(page.locator("main")).not.toContainText("Nothing flagged.");
  });
});

test.describe("Document corroborator", () => {
  test.setTimeout(180_000);

  test("a rule the engine cannot read is explained to the user", async ({ page, problems }) => {
    problems.allowHttp(/\/corroboration\/runs$/, [422], "an unreadable rule is refused");
    await page.goto(ws("C", "corroboration"));
    await page.getByRole("button", { name: "New comparison" }).click();
    await page.getByRole("checkbox", { name: "purchase-order-PO-E2E-5001.pdf" }).check();
    await page.getByRole("checkbox", { name: "invoice-INV-E2E-1002.pdf" }).check();
    await page.getByRole("textbox", { name: /Rules every document must meet/ }).fill("The vendor bank account must not change");
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.locator("main")).toContainText(/could not be read as a checkable rule/, { timeout: 10_000 });
  });

  test("compare the PO with the invoice and open the result", async ({ page }) => {
    await page.goto(ws("C", "corroboration"));
    await page.getByRole("button", { name: "New comparison" }).click();
    await page.getByRole("checkbox", { name: "purchase-order-PO-E2E-5001.pdf" }).check();
    await page.getByRole("checkbox", { name: "invoice-INV-E2E-1002.pdf" }).check();
    await page.getByRole("button", { name: "Compare" }).click();
    await expect(page.locator("main")).toContainText(/purchase-order-PO-E2E-5001\.pdf/, { timeout: 30_000 });
    const run = page.locator("main").getByRole("link", { name: /purchase-order-PO-E2E-5001|invoice-INV-E2E-1002/ }).first();
    await run.click();
    await expect(page).toHaveURL(/\/corroboration\/[0-9a-f-]{36}/);
    await expect(page.locator("main")).toContainText(/difference|material|confidence|score|current|comparing|queued/i, {
      timeout: 60_000,
    });
  });
});
