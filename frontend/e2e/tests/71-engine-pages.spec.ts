/**
 * ENGINE PAGES AND ACTIONS THAT WERE ONLY PROVEN TO RENDER (campaign session 4, CAMPAIGN-MAP §4.1).
 *
 * The page smoke opens every page; this uses the ones whose work had never been done in a browser:
 * an assistant conversation beside its document, an agent's proposal approved on its own page, an
 * ERP posting's page and its decisions, the redaction studio's apply and downloads, the review hub's take and assign,
 * a clause review decided in the assertion console, the sidebar's notifications link,
 * the command palette's "Create workspace", the legacy profile and account addresses, and a
 * failure message on mutations F-145 listed as silent.
 *
 * Each uses a seeded Enterprise person (Tenant C) for reading, or a new Enterprise customer of its
 * own when it changes settings, so nothing here changes what other tests see.
 */
import fs from "node:fs";

import type { Page } from "@playwright/test";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, type ApiSession } from "../support/api";
import { API_BASE, STATE_FILE, runId, ws } from "../support/env";
import { addMember, newCustomer, signInAs, standIn, type Customer } from "../support/journey";
import { buildPdf, DISPUTED_INVOICE_PAGE, INVOICE_1001_RESCAN } from "../support/sample-docs";

async function enterprise(label: string): Promise<Customer> {
  const customer = await newCustomer(label, `${label} Ltd`);
  standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "10");
  return customer;
}

async function upload(customer: Customer, name: string, body: Buffer): Promise<string> {
  const form = new FormData();
  form.append("file", new Blob([new Uint8Array(body)], { type: "application/pdf" }), `${name}-${runId()}.pdf`);
  const response = await fetch(`${API_BASE}/workspaces/${customer.wsId}/work-items`, {
    method: "POST", headers: { Authorization: `Bearer ${customer.session.accessToken}` }, body: form,
  });
  expect(response.status, await response.clone().text()).toBe(201);
  return String(((await response.json()) as { id: string }).id);
}

async function settled(session: ApiSession, workspaceId: string, id: string): Promise<void> {
  await expect.poll(async () => {
    const item = await api<{ status?: string }>(session, "GET", `/workspaces/${workspaceId}/work-items/${id}`);
    return String(item.body.status ?? "");
  }, { timeout: 180_000, intervals: [2_000] }).not.toMatch(/^(PENDING|QUEUED|PROCESSING|UPLOADED|)$/);
}

function seeded(name: string): string {
  const state = JSON.parse(fs.readFileSync(STATE_FILE, "utf8"));
  const found = (state.documents.C.items as Array<{ id: string; name: string }>).find((item) => item.name.includes(name));
  expect(found, name).toBeTruthy();
  return found!.id;
}

async function failNext(page: Page, pattern: RegExp, method: string): Promise<void> {
  await page.route(pattern, async (route) => {
    if (route.request().method() !== method) return route.continue();
    await route.fulfill({ status: 503, json: { detail: "Service temporarily unavailable." } });
  });
}

test.describe("Engine pages that do their work", () => {
  test.setTimeout(300_000);

  test.describe("as an Enterprise contributor", () => {
    test.use({ user: "C.member" });

    test("a conversation about one document is answered beside that document", async ({ page, session }) => {
      const document = seeded("INV-E2E-1001");
      const workspaceId = String((await api<{ organizations: Array<{ workspaces: Array<{ id: string; slug: string }> }> }>(
        session, "GET", "/me/context")).body.organizations.flatMap((o) => o.workspaces).find((w) => w.slug === "operations")?.id);
      const created = await api<{ id: string }>(session, "POST", `/workspaces/${workspaceId}/assistant/conversations`,
        { title: "Canvas", work_item_id: document });
      expect(created.status, created.text).toBe(201);
      await page.goto(ws("C", `assistant/c/${created.body.id}?doc=${document}`));
      // The document beside the conversation is an <aside>: a complementary landmark.
      await expect(page.getByRole("complementary", { name: "Source document" })).toBeVisible({ timeout: 20_000 });
      await page.getByPlaceholder("Ask about your documents…").fill("What is the invoice number and who is the vendor?");
      await page.getByRole("button", { name: "Send" }).click();
      await expect(page.getByRole("region", { name: "Conversation" })).toContainText(/INV-E2E-1001|Acme/, { timeout: 60_000 });
      await expectHealthyPage(page);
    });

    test("the sidebar's Notifications link opens the workspace's notifications", async ({ page }) => {
      await page.goto(ws("C", ""));
      await page.getByRole("navigation").getByRole("link", { name: "Notifications", exact: true }).first().click();
      await expect(page).toHaveURL(/\/caretakers-global\/operations\/notifications/);
      await expect(page.locator("main")).toContainText(/Notifications/);
      await expectHealthyPage(page);
    });
  });

  test.describe("as an Enterprise owner", () => {
    test.use({ user: "C.owner" });

    test("the command palette's Create workspace opens the new-workspace form", async ({ page }) => {
      await page.goto(ws("C", ""));
      await settle(page);
      await page.keyboard.press("Control+k");
      const palette = page.getByRole("dialog");
      await expect(palette).toBeVisible();
      await page.keyboard.type("create workspace");
      await page.keyboard.press("Enter");
      await expect(page).toHaveURL(/\/workspaces\/new/, { timeout: 10_000 });
      await expect(page.getByLabel("Workspace name")).toBeVisible();
      await expectHealthyPage(page);
    });

    test("the old profile and account addresses lead into the app, not to an error", async ({ page }) => {
      for (const legacy of ["/profile", "/account"]) {
        await page.goto(legacy);
        await settle(page);
        // F-358. To the person's profile, now a section of the workspace's settings.
        await expect(page).toHaveURL(/\/[a-z0-9-]+\/[a-z0-9-]+\/settings\?section=profile$/);
        await expect(page.locator("main")).toContainText("Profile");
        await expect(page.locator("main")).not.toContainText("Page not found");
        await expectHealthyPage(page);
      }
    });

    test("the redaction studio applies the redactions and offers the redacted PDF and its manifest", async ({ page }) => {
      await page.goto(ws("C", `work-items/${seeded("INV-E2E-1001")}`));
      await page.getByRole("button", { name: "Redact" }).click();
      await page.getByRole("menu", { name: "Redaction profile" }).getByRole("menuitem").first().click();
      await expect(page).toHaveURL(/\/redactions\/[0-9a-f-]{36}/, { timeout: 60_000 });
      const apply = page.getByRole("button", { name: /^Apply \d+ redactions?$/ });
      await expect(apply).toBeEnabled({ timeout: 60_000 });
      await apply.click();
      await page.getByRole("button", { name: "Apply", exact: true }).click();
      const pdf = page.getByRole("button", { name: "Download redacted PDF" });
      await expect(pdf).toBeVisible({ timeout: 120_000 });
      for (const [button, extension] of [[pdf, /\.pdf$/], [page.getByRole("button", { name: "Download manifest" }), /\.json$/]] as const) {
        const download = page.waitForEvent("download", { timeout: 30_000 });
        await button.click();
        expect((await download).suggestedFilename()).toMatch(extension);
      }
      await expectHealthyPage(page);
    });

    test("mutations that used to fail silently say so (F-145)", async ({ page, problems }) => {
      problems.allowHttp(/\/api\/v1\//, [503], "the server is made to refuse each mutation once");
      // A domain claim on the identity console.
      await failNext(page, /\/identity\/domains$/, "POST");
      await page.goto("/organizations/caretakers-global/identity?tab=domains");
      await page.getByLabel("Domain to claim").fill(`f145-${runId()}.example.com`);
      await page.getByRole("button", { name: "Claim domain" }).click();
      await expect(page.getByText(/temporarily unavailable|could not|couldn.t/i).first()).toBeVisible({ timeout: 10_000 });
      // A holiday calendar on the obligations page.
      await failNext(page, /\/holiday-calendars$/, "POST");
      await page.goto(ws("C", "obligations"));
      await page.getByRole("tab", { name: "Holiday calendars" }).click();
      const calendar = page.getByRole("textbox").first();
      await calendar.fill(`F-145 calendar ${runId()}`);
      await page.getByRole("button", { name: "Save calendar" }).click();
      await expect(page.getByText(/temporarily unavailable|could not|couldn.t/i).first()).toBeVisible({ timeout: 10_000 });
    });
  });

  test.describe("for an Enterprise customer's own work", () => {
    test("an agent's proposal opens on its own page and is approved there", async ({ page }) => {
      const customer = await enterprise("proposal-page");
      expect((await api(customer.session, "PUT", `/workspaces/${customer.wsId}/document-settings/`,
        { verification_enabled: true, verification_agents: 3 })).status).toBe(200);
      const disputed = await upload(customer, "disputed", buildPdf([DISPUTED_INVOICE_PAGE]));
      await settled(customer.session, customer.wsId, disputed);
      expect((await api(customer.session, "PUT", `/workspaces/${customer.wsId}/process/agent/policy`,
        { planning_enabled: true })).status).toBe(200);
      // The three agents' disagreement reaches the review queue a little after the document settles;
      // each sweep reads the queue as it is, so sweep until the agent has proposed something.
      let proposal: { id: string; status: string } | undefined;
      await expect.poll(async () => {
        expect((await api(customer.session, "POST", `/workspaces/${customer.wsId}/process/sweep`)).status).toBe(200);
        const proposals = await api<{ items: Array<{ id: string; status: string }> }>(customer.session, "GET",
          `/workspaces/${customer.wsId}/process/agent/proposals`);
        proposal = proposals.body.items.find((item) => item.status === "PROPOSED");
        return Boolean(proposal);
      }, { timeout: 120_000, intervals: [3_000] }).toBe(true);

      await signInAs(page, customer.email);
      await page.goto(customer.ws(`process/proposals/${proposal!.id}`));
      await page.getByRole("button", { name: /^Approve/ }).click();
      await expect.poll(async () => (await api<{ proposal: { status: string } }>(customer.session, "GET",
        `/workspaces/${customer.wsId}/process/agent/proposals/${proposal!.id}`)).body.proposal.status,
      { timeout: 90_000, intervals: [2_000] }).toBe("APPLIED");
      await expect(page.locator("main")).toContainText("Applied (1)", { timeout: 30_000 });
      await expectHealthyPage(page);
    });

    test("the review hub: take an item, then hand it to a colleague", async ({ page }) => {
      const customer = await enterprise("review-hub");
      const colleague = await addMember(customer, "review-colleague", "ADMIN", "ADMIN");
      expect((await api(customer.session, "PUT", `/workspaces/${customer.wsId}/document-settings/`,
        { verification_enabled: true, verification_agents: 3 })).status).toBe(200);
      const disputed = await upload(customer, "review", buildPdf([DISPUTED_INVOICE_PAGE]));
      await settled(customer.session, customer.wsId, disputed);

      await signInAs(page, customer.email);
      await page.goto(customer.ws("verification"));
      const items = page.getByRole("list", { name: "Review items" });
      await expect(items.getByRole("listitem")).not.toHaveCount(0, { timeout: 60_000 });
      await items.getByRole("button", { name: "Take it" }).first().click();
      await expect(items).toContainText(customer.email, { timeout: 15_000 });  // taken: its claimant shows
      await items.getByRole("button", { name: "Select" }).first().click();
      await page.getByRole("combobox", { name: "Assign selected to" }).selectOption({ label: colleague.email });
      await expect(items).toContainText(colleague.email.split("@")[0]!.slice(0, 12), { timeout: 15_000 });
      await expectHealthyPage(page);
    });

    test("a clause review is decided in the assertion console", async ({ page }) => {
      const customer = await enterprise("clause-review");
      const check = await api<{ rule_id: string; node_key: string }>(customer.session, "POST",
        `/workspaces/${customer.wsId}/assertions/clause-checks`, { name: "Notice at least 30 days" });
      expect(check.status, check.text).toBe(201);
      const saved = await api(customer.session, "PUT",
        `/workspaces/${customer.wsId}/assertions/rules/${check.body.rule_id}/nodes/${check.body.node_key}`,
        { node_key: check.body.node_key, sentence: "The notice period for termination must be at least 30 days.", threshold: "0.8" });
      expect(saved.status, saved.text).toBe(200);
      const contract = await upload(customer, "contract", buildPdf([[
        "SERVICES AGREEMENT", "between Contoso Retail and Acme Supplies Ltd",
        "1. Services. Acme shall provide maintenance services.",
        "2. Termination. Either party may terminate this Agreement by giving 15 days' written notice.",
        "3. Governing law. India."]]));
      await settled(customer.session, customer.wsId, contract);
      await expect.poll(async () => (await api<{ items?: unknown[] } | unknown[]>(customer.session, "GET",
        `/workspaces/${customer.wsId}/assertions/reviews`)).text, { timeout: 120_000, intervals: [3_000] }).toMatch(/evaluation_id/);

      await signInAs(page, customer.email);
      await page.goto(customer.ws("assertions"));
      await page.getByRole("button", { name: "It fails" }).first().click();
      await expect.poll(async () => (await api(customer.session, "GET",
        `/workspaces/${customer.wsId}/assertions/reviews`)).text, { timeout: 20_000 }).not.toMatch(/evaluation_id/);
      await expectHealthyPage(page);
    });
  });
});

test.describe("Old addresses and the not-found page", () => {
  test.use({ user: "C.owner" });

  test("a saved flat address opens the same page inside the workspace, with its query", async ({ page }) => {
    // The workspace the person last used is where flat addresses land (here: operations).
    await page.goto(ws("C", ""));
    await settle(page);
    const document = seeded("INV-E2E-1001");
    const cases: Array<[string, RegExp]> = [
      ["/", /\/caretakers-global\/operations\/?$/],
      [`/work-items/${document}`, new RegExp(`/caretakers-global/operations/work-items/${document}$`)],
      ["/assistant", /\/caretakers-global\/operations\/assistant$/],
      ["/automation", /\/caretakers-global\/operations\/automation$/],
      ["/notifications", /\/caretakers-global\/operations\/notifications$/],
      ["/settings?section=ai", /\/caretakers-global\/operations\/settings\?section=ai$/],
    ];
    for (const [legacy, landed] of cases) {
      await page.goto(legacy);
      await expect(page, legacy).toHaveURL(landed, { timeout: 15_000 });
      await expect(page.locator("main"), legacy).not.toContainText("Page not found");
    }
    await page.goto(`/work-items/${document}`);
    await expect(page.locator("main")).toContainText("invoice-INV-E2E-1001.pdf", { timeout: 20_000 });
    await expectHealthyPage(page);
  });

  test("an unknown address says so and its button leads back into the app", async ({ page, problems }) => {
    problems.allowHttp(/./, [404], "the address does not exist");
    await page.goto(`/no-such-page-${runId()}`);
    await expect(page.getByText("Page not found")).toBeVisible();
    await page.getByRole("link", { name: "Return to Dashboard" }).click();
    await expect(page).toHaveURL(/\/caretakers-global\/[a-z0-9-]+\/?$/, { timeout: 15_000 });
    await expect(page.locator("main")).not.toContainText("Page not found");
  });
});

test.describe("An ERP posting's own page", () => {
  test.setTimeout(300_000);

  test("shows what was posted, why it failed, and records the ERP's own reference", async ({ page }) => {
    // A new Enterprise customer of its own: an invoice whose line-item table a person accepts is an
    // approved outcome, ready to post (erp/sources.eligible), without touching Tenant C's data.
    const customer = await enterprise("erp-posting-page");
    const invoice = await upload(customer, "erp-invoice", buildPdf(INVOICE_1001_RESCAN));
    await settled(customer.session, customer.wsId, invoice);
    let tableId = "";
    await expect.poll(async () => {
      const tables = await api<{ tables: Array<{ id: string }> }>(customer.session, "GET",
        `/workspaces/${customer.wsId}/work-items/${invoice}/tables`);
      tableId = tables.body.tables?.[0]?.id ?? "";
      return tableId;
    }, { timeout: 120_000, intervals: [2_000] }).not.toBe("");
    const accepted = await api(customer.session, "POST", `/workspaces/${customer.wsId}/tables/${tableId}/review`,
      { verdict: "ACCEPT" });
    expect(accepted.status, accepted.text).toBe(200);

    const ready = await api<{ items: Array<{ kind: string; id: string; objects: string[] }> }>(
      customer.session, "GET", `/workspaces/${customer.wsId}/erp/outcomes`);
    expect(ready.status, ready.text).toBe(200);
    const outcome = ready.body.items[0];
    expect(outcome, `nothing is ready to post: ${ready.text.slice(0, 300)}`).toBeTruthy();
    const target = await api<{ target: { id: string } }>(customer.session, "POST", `/workspaces/${customer.wsId}/erp/targets`,
      { name: `Posting page ${runId()}`, format: "CSV", transport: "DOWNLOAD" });
    expect(target.status, target.text).toBe(201);
    const posted = await api<{ results: Array<{ posting_id: string }> }>(customer.session, "POST",
      `/workspaces/${customer.wsId}/erp/postings`,
      { target_id: target.body.target.id, source_kind: outcome!.kind, source_id: outcome!.id,
        object_kinds: outcome!.objects.slice(0, 1) });
    expect(posted.status, posted.text).toBe(201);

    await signInAs(page, customer.email);
    await page.goto(customer.ws(`erp/postings/${posted.body.results[0]!.posting_id}`));
    const main = page.locator("main");
    await expect(main).toContainText("Vendor bill", { timeout: 30_000 });
    // The accepted table's subtotal line has no amount, so the bill is refused with that reason:
    // the page says so and offers the decisions. Someone who fixed it in the ERP records it here.
    await expect(main.getByRole("heading", { name: "Attempts" })).toBeVisible();
    await page.getByLabel("ERP reference (when it has it)").fill("BILL-E2E-1043");
    await page.getByRole("button", { name: "The ERP has it" }).click();
    await expect(main).toContainText("BILL-E2E-1043", { timeout: 30_000 });
    await expect(page.getByRole("button", { name: "The ERP has it" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
