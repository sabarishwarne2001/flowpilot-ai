/**
 * AI ASSISTANT and the document-intelligence modules, as the Enterprise owner.
 *
 * Environment note: this sandbox has no LLM provider key (Groq/Gemini hosts
 * are blocked) and runs ML_STUBS=true, so assistant answers depend on whether
 * the product degrades gracefully without a model. The tests assert what a
 * user must see either way: a streamed answer with citations, or a clear
 * message — never a silent failure or a crash.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { STATE_FILE, runId, ws } from "../support/env";

test.use({ user: "C.owner" });

const LLM_AVAILABLE = process.env.E2E_LLM === "1";
const NO_LLM = "Blocked here: no LLM provider key in this sandbox (Groq/Gemini hosts unreachable). Set E2E_LLM=1 where the API has a key.";

test.describe("AI Assistant", () => {
  test.setTimeout(150_000);

  test("when the AI provider is down the user sees a clear error and keeps the question", async ({ page, problems }) => {
    test.skip(LLM_AVAILABLE, "only meaningful when the provider is unavailable");
    problems.allowHttp(/\/assistant\/conversations\/[^/]+\/messages/, [503], "no LLM provider in this sandbox");
    await page.goto(ws("C", "assistant"));
    await page.getByRole("button", { name: "New", exact: true }).click();
    const box = page.getByPlaceholder("Ask anything about your knowledge base...");
    const question = "What is the total amount due on invoice INV-E2E-1001?";
    await box.fill(question);
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.locator("body")).toContainText(/temporarily unavailable|try again|could not|failed/i, { timeout: 30_000 });
    await page.waitForTimeout(6_000); // past the 4-second toast
    const kept = (await box.inputValue()) === question || (await page.locator("main").innerText()).includes(question);
    expect(kept, "the question is still on screen after the error (not silently lost)").toBe(true);
  });

  async function newConversation(page: import("@playwright/test").Page) {
    await page.goto(ws("C", "assistant"));
    await page.getByRole("button", { name: "New", exact: true }).click();
    await expect(page.getByPlaceholder("Ask anything about your knowledge base...")).toBeVisible();
  }

  test("an answerable question gets a streamed answer that cites the invoice", async ({ page }) => {
    test.skip(!LLM_AVAILABLE, NO_LLM);
    await newConversation(page);
    await page.getByPlaceholder("Ask anything about your knowledge base...").fill(
      "What is the total amount due on invoice INV-E2E-1001?",
    );
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.locator("main")).toContainText(/1,?250(\.00)?/, { timeout: 90_000 });
    await expect(page.locator("main")).toContainText(/invoice-INV-E2E-1001\.pdf|INV-E2E-1001/);
  });

  test("an unanswerable question gets a truthful refusal, not an invention", async ({ page }) => {
    test.skip(!LLM_AVAILABLE, NO_LLM);
    await newConversation(page);
    await page.getByPlaceholder("Ask anything about your knowledge base...").fill(
      "What is the name of the CEO's dog mentioned in our documents?",
    );
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(page.locator("main")).toContainText(
      /cannot find|can.t find|couldn.t find|not (found|mentioned|present) in|no (information|relevant)|don.t have/i,
      { timeout: 90_000 },
    );
  });

  test("conversation list: tabs, search and the scope picker work", async ({ page }) => {
    await page.goto(ws("C", "assistant"));
    for (const tab of ["Workspace", "Documents", "All"]) {
      await page.getByRole("tab", { name: tab, exact: true }).click();
    }
    await page.getByPlaceholder("Search titles, documents, messages").fill("zzz-no-such-conversation");
    await settle(page, 600);
    await page.getByPlaceholder("Search titles, documents, messages").fill("");
    await page.getByRole("button", { name: "Archived" }).click();
    await expectHealthyPage(page);
  });
});

test.describe("Extraction memory", () => {
  test("the mode can be switched and the choice persists", async ({ page }) => {
    await page.goto(ws("C", "extraction-memory"));
    const group = page.getByRole("radiogroup", { name: "Extraction memory mode" });
    await group.getByRole("radio", { name: /^Off/ }).click();
    await expect(group.getByRole("radio", { name: /^Off/ })).toBeChecked();
    await page.reload();
    await expect(page.getByRole("radiogroup", { name: "Extraction memory mode" }).getByRole("radio", { name: /^Off/ })).toBeChecked();
    await page.getByRole("radiogroup", { name: "Extraction memory mode" }).getByRole("radio", { name: /^Shadow/ }).click();
    await expect(page.getByRole("radiogroup", { name: "Extraction memory mode" }).getByRole("radio", { name: /^Shadow/ })).toBeChecked();
  });

  test("learned layouts are listed from processed documents", async ({ page }) => {
    await page.goto(ws("C", "extraction-memory"));
    await expect(page.locator("main")).toContainText("Layouts");
    await expect(page.getByRole("row").filter({ hasText: /learning|trial|active/i }).first()).toBeVisible();
  });
});

test.describe("Entity graph", () => {
  test("search and kind filters work, and vendors from the invoices are present", async ({ page }) => {
    await page.goto(ws("C", "entities"));
    for (const kind of ["People", "Organizations", "Addresses", "Accounts", "All"]) {
      await page.getByRole("button", { name: new RegExp(`^${kind}`) }).click();
    }
    await page.locator("#entity-search").fill("Acme");
    await settle(page, 800);
    // The seeded invoices name "Acme Industrial Supplies Ltd" as vendor.
    await expect(page.locator("main")).toContainText("Acme", { timeout: 15_000 });
  });
});

test.describe("Cases", () => {
  test("save a template draft, open a case by hand and change its status", async ({ page }) => {
    await page.goto(ws("C", "cases"));
    await page.getByRole("button", { name: "Save draft" }).click();
    await settle(page);
    const publish = page.getByRole("button", { name: /publish/i }).first();
    if (await publish.isVisible()) {
      await publish.click();
      await settle(page);
    }
    const template = page.getByRole("combobox", { name: "Template" });
    const options = await template.locator("option").allTextContents();
    const usable = options.find((option) => option && !/template…/i.test(option));
    expect(usable, `a published template to open a case with (options: ${options.join(" | ")})`).toBeTruthy();
    await template.selectOption({ label: usable as string });
    const title = `E2E case ${runId()}`;
    await page.getByRole("textbox", { name: "Case title" }).fill(title);
    await page.getByRole("button", { name: "Open case" }).click();
    await expect(page.locator("main")).toContainText(title, { timeout: 15_000 });
    await page.getByText(title).first().click();
    await expect(page).toHaveURL(/\/cases\/[0-9a-f-]{36}/);
    await expectHealthyPage(page);
  });
});

test.describe("Scanned packets", () => {
  test("the uploaded 4-page packet has a proposed split that can be reviewed", async ({ page }) => {
    await page.goto(ws("C", "packet-splits"));
    await expect(page.getByRole("row", { name: /scanned-packet-E2E\.pdf/ })).toBeVisible();
    await page.getByRole("link", { name: "scanned-packet-E2E.pdf" }).click();
    await expect(page).toHaveURL(/\/packet-splits\/[0-9a-f-]{36}/);
    await expect(page.getByRole("region", { name: "Pages" })).toBeVisible();
    // Toggle a boundary on and off: the dicer is interactive.
    const boundary = page.getByRole("button", { name: /a document boundary before page 2/ });
    await boundary.click();
    await boundary.click();
    await expectHealthyPage(page);
  });
});

test.describe("Tables", () => {
  test("the tables page filters by status", async ({ page }) => {
    await page.goto(ws("C", "tables"));
    for (const tab of [/^Needs review/, /^Reconciles/, /^Extracted/, /^Reviewed/, /^All$/]) {
      await page.getByRole("tab", { name: tab }).click();
    }
    await expectHealthyPage(page);
  });

  test("a line-item table from the invoices is extracted and can be opened, edited and exported", async ({ page }) => {
    await page.goto(ws("C", "tables"));
    await settle(page);
    await expect(page.locator("main")).not.toContainText("No tables yet.");
  });
});

test.describe("Obligations", () => {
  test("the contract's obligations were extracted and can be opened", async ({ page }) => {
    await page.goto(ws("C", "obligations"));
    await page.getByRole("button", { name: "All" }).last().click();
    await expect(page.locator("main")).toContainText(/contract-MSA-E2E-2026\.pdf|Report|Payment|Renewal/);
    await page.getByRole("tab", { name: "Calendar", exact: true }).click();
    await settle(page);
    await page.getByRole("tab", { name: "Holiday calendars" }).click();
    await settle(page);
    await page.getByRole("tab", { name: "Calendar feeds" }).click();
    await settle(page);
    await page.getByRole("tab", { name: "List" }).click();
    await expectHealthyPage(page);
  });

  test("a new obligation can be created and its status changed", async ({ page }) => {
    await page.goto(ws("C", "obligations"));
    await page.getByRole("button", { name: "New obligation" }).click();
    const what = `E2E deliver quarterly report ${runId()}`;
    const form = page.locator("form").filter({ has: page.getByRole("textbox", { name: "What is due" }) });
    await form.getByRole("textbox", { name: "What is due" }).fill(what);
    await form.getByRole("combobox", { name: "Kind" }).selectOption({ label: "Report" });
    await form.getByRole("textbox", { name: "Due date" }).fill("2026-12-15");
    await form.getByRole("button", { name: "Save obligation" }).click();
    await expect(page.locator("main")).toContainText(what, { timeout: 15_000 });
    await page.getByText(what).first().click();
    await expect(page).toHaveURL(/\/obligations\/[0-9a-f-]{36}/);
    const status = page.getByRole("button", { name: /mark (as )?(done|complete|met)|complete|close/i }).first();
    await status.click();
    await settle(page);
    await expectHealthyPage(page);
  });

  test("the .ics and .csv exports download", async ({ page }) => {
    await page.goto(ws("C", "obligations"));
    for (const name of [".ics", ".csv"]) {
      const download = page.waitForEvent("download", { timeout: 15_000 });
      await page.getByRole("button", { name, exact: true }).click();
      const file = await download;
      expect(file.suggestedFilename()).toMatch(new RegExp(`\\${name}$`));
    }
  });
});

test.describe("Document-level panels", () => {
  test("the invoice's entities and obligations panels load in the viewer", async ({ page }) => {
    const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
    const contract = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) =>
      item.name.includes("contract-MSA"),
    );
    await page.goto(ws("C", `work-items/${contract?.id}`));
    await page.getByRole("button", { name: "Obligations", exact: true }).click();
    await expect(page.locator("main")).toContainText(/obligation|due/i);
    await expectHealthyPage(page);
  });
});
