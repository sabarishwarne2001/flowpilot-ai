/**
 * HELM, the flagship engine: the walking skeleton end to end (brief §12 step 1), on a tenant of its
 * own.
 *
 * A new Business customer uploads two supplier invoices. In the browser the owner finds the
 * Documents | Helm switch in the header (a radio group), switches to Helm, starts Helm on the
 * workspace and walks the four-step first run: who the company is, its cash, two of the mandates
 * recommended for its kind of business (each starts in Propose mode), and the replay that runs by
 * itself. Then it re-plans. The second invoice asks to be paid into a new bank account,
 * and the radar marks one of the pair as the same bill again (which one depends on which the worker
 * read last): when they are different bills the Guard blocks paying the changed account and says
 * why; when they are the same bill Helm plans to pay only the other. The owner approves Helm's
 * proposal to hold the duplicate, the worker runs it after the Guard checks it again, and its
 * certificate's fingerprint verifies. A member sees no switch, and the API refuses them Helm.
 */
import { test, expect, settle } from "../support/fixtures";
import { api, listWorkItems, uploadFile } from "../support/api";
import { addMember, newCustomer, signInAs, standIn, type Customer } from "../support/journey";
import { samplePath, type SampleName } from "../support/sample-docs";

const SAMPLES: SampleName[] = ["invoice1001", "invoice1002"];
const TERMINAL = new Set(["COMPLETED", "FAILED", "ERROR", "NEEDS_REVIEW", "REVIEW", "PENDING_REVIEW"]);
const NEEDS_MODEL = "needs extracted fields: run with E2E_LLM=1 (support/llm-mock.mjs)";

interface Action {
  readonly id: string;
  readonly kind: string;
  readonly state: string;
  readonly guard_passed: boolean;
  readonly title: string;
}

let customer: Customer;

async function actions(bookId: string): Promise<Action[]> {
  const cockpit = await api<{ actions: Action[]; recent: Action[] }>(
    customer.session, "GET", `/organizations/${customer.orgId}/helm/books/${bookId}/cockpit`,
  );
  expect(cockpit.status, cockpit.text.slice(0, 300)).toBe(200);
  return [...cockpit.body.actions, ...cockpit.body.recent];
}

/** The invoice numbers the radar marked: the probable duplicate, and the one whose bank account changed. */
async function flagged(): Promise<{ duplicate: string; changed: string }> {
  const numberOf = new Map<string, string>();
  for (const item of await listWorkItems(customer.session, customer.wsId)) {
    const match = /INV-E2E-\d+/.exec(String(item.original_filename ?? ""));
    if (match) numberOf.set(String(item.id), match[0]);
  }
  const radar = await api<{ items: Array<{ kind: string; subject_work_item_id: string }> }>(
    customer.session, "GET", `/workspaces/${customer.wsId}/anomalies`,
  );
  const risk = await api<{ items: Array<{ kind: string; work_item_id: string }> }>(
    customer.session, "GET", `/workspaces/${customer.wsId}/payment-risk`,
  );
  const duplicate = numberOf.get(radar.body.items.find((f) => f.kind === "DUPLICATE_DOCUMENT")?.subject_work_item_id ?? "");
  const changed = numberOf.get(risk.body.items.find((f) => f.kind === "BANK_ACCOUNT_CHANGED")?.work_item_id ?? "");
  expect(duplicate, "the radar's duplicate finding").toBeTruthy();
  expect(changed, "the bank-account flag").toBeTruthy();
  return { duplicate: duplicate as string, changed: changed as string };
}

test.describe("Helm skeleton — a tenant of its own", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(async () => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    test.setTimeout(300_000);
    customer = await newCustomer("helm", "Helm Skeleton Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "3");
    for (const sample of SAMPLES) {
      const upload = await uploadFile(customer.session, customer.wsId, samplePath(sample));
      expect(upload.status, upload.text.slice(0, 300)).toBe(201);
    }
    await expect
      .poll(async () => {
        const items = await listWorkItems(customer.session, customer.wsId);
        return items.length === SAMPLES.length && items.every((item) => TERMINAL.has(String(item.status).toUpperCase()));
      }, { timeout: 240_000, intervals: [2_000, 3_000] })
      .toBe(true);
    // The radar reads them after processing: the new bank account and the probable duplicate.
    await expect
      .poll(async () => {
        const risk = await api<{ items: unknown[] }>(customer.session, "GET", `/workspaces/${customer.wsId}/payment-risk`);
        const radar = await api<{ items: Array<{ kind: string }> }>(customer.session, "GET", `/workspaces/${customer.wsId}/anomalies`);
        return (risk.body.items?.length ?? 0) > 0 && (radar.body.items ?? []).some((f) => f.kind === "DUPLICATE_DOCUMENT");
      }, { timeout: 180_000, intervals: [2_000, 3_000] })
      .toBe(true);
  });

  test("switch to Helm, start it, add mandates, re-plan, see what the Guard passes and blocks, and approve a hold", async ({ page }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    await signInAs(page, customer.email);
    await page.goto(customer.ws());
    await settle(page);

    // The mode switch: a radio group in the header, Documents checked.
    const mode = page.getByRole("radiogroup", { name: "Mode" });
    await expect(mode).toBeVisible();
    await expect(mode.getByRole("radio", { name: "Documents" })).toHaveAttribute("aria-checked", "true");
    await mode.getByRole("radio", { name: /^Helm/ }).click();

    // No book yet: Helm begins at Start.
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/helm/start$`));
    await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ }))
      .toHaveAttribute("aria-checked", "true");
    await expect(page.getByRole("heading", { name: "Helm keeps your business promises" })).toBeVisible();
    await page.getByRole("button", { name: "Start Helm on Main" }).click();

    // The first run, step 1: who we are (the invoices are billed to Caretakers Global Inc).
    await expect(page).toHaveURL(/\/helm\/setup\?book=/);
    await expect(page.getByRole("heading", { name: "Set up Helm on Main", level: 1 })).toBeVisible();
    await expect(page.getByRole("heading", { name: "Who you are", level: 2 })).toBeFocused();
    await page.getByRole("textbox", { name: "Legal name 1" }).fill("Caretakers Global Inc");
    const confirmed = page.waitForResponse((r) => /\/helm\/books\/[^/]+\/settings$/.test(r.url()) && r.request().method() === "PATCH");
    await page.getByRole("button", { name: "These are us" }).click();
    expect((await confirmed).status()).toBe(200);

    // Step 2: the cash, typed by a person.
    await expect(page.getByRole("heading", { name: "Your cash", level: 2 })).toBeVisible();
    await page.getByRole("textbox", { name: "Currency" }).fill("USD");
    await page.getByRole("textbox", { name: "Amount" }).fill("20000");
    const cash = page.waitForResponse((r) => /\/cash-positions$/.test(r.url()) && r.request().method() === "POST");
    await page.getByRole("button", { name: "Save the cash" }).click();
    expect((await cash).status()).toBe(201);

    // Step 3: the kind of business, then two of the mandates recommended for it.
    await expect(page.getByRole("heading", { name: "Your mandates", level: 2 })).toBeVisible();
    await page.getByRole("radio", { name: "Something else" }).check();
    const wanted = ["Pay every supplier on time", "Never pay a duplicate"];
    for (const box of await page.getByRole("checkbox").all()) {
      const label = await box.evaluate((el) => el.closest("label")?.textContent ?? "");
      if (wanted.some((title) => label.startsWith(title))) {
        await box.check();
      } else {
        await box.uncheck();
      }
    }
    let created = 0;
    page.on("response", (r) => {
      if (/\/helm\/books\/[^/]+\/mandates$/.test(r.url()) && r.request().method() === "POST" && r.status() === 201) created += 1;
    });
    await page.getByRole("button", { name: "Add these in Propose mode" }).click();

    // Step 4: the replay runs by itself and shows what Helm would have held.
    await expect(page.getByRole("heading", { name: "What Helm would have held", level: 2 })).toBeVisible();
    expect(created).toBe(2);
    await expect(page.getByTestId("helm-backtest-report")).toBeVisible({ timeout: 60_000 });
    await page.getByRole("button", { name: "Open the cockpit" }).click();
    await expect(page).toHaveURL(/\/helm\?book=/);

    // The cash is shown as typed by a person; both mandates are active, in Propose mode.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Settings" }).click();
    await expect(page.getByText("typed by a person")).toBeVisible();
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Mandates" }).click();
    await expect(page.getByRole("heading", { name: "Mandates", level: 1 })).toBeVisible();
    await expect(page.getByTestId("helm-mandate")).toHaveCount(2);
    for (const card of await page.getByTestId("helm-mandate").all()) {
      await expect(card).toContainText("Active");
      await expect(card).toContainText("Propose");
    }

    // The cockpit: re-plan now, then approve the first payment the Guard passed.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Cockpit" }).click();
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
    const planned = page.waitForResponse((r) => /\/helm\/books\/[^/]+\/plan$/.test(r.url()) && r.request().method() === "POST");
    await page.getByRole("button", { name: "Re-plan now" }).click();
    expect((await planned).status()).toBe(200);
    await expect(page.getByTestId("helm-instrument")).toHaveCount(2);
    const rows = page.getByTestId("helm-action");
    await expect(rows.first()).toBeVisible();

    // INV-E2E-1002, uploaded second, asks to be paid into a new bank account. Which of the pair the
    // radar marks as the same bill again depends on which the worker read last, so ask it.
    const { duplicate, changed } = await flagged();
    const pays = rows.filter({ hasText: "Pay Acme Industrial Supplies" });
    if (duplicate !== changed) {
      // The changed account is on the bill Helm would pay: the Guard blocks its payment and says why.
      const blocked = pays.filter({ hasText: "Blocked by the Guard" }).first();
      await expect(blocked).toBeVisible();
      await expect(blocked.getByRole("button", { name: /^Approve: / })).toHaveCount(0);
      await blocked.getByRole("button", { name: "Why, and what the Guard checked" }).click();
      await expect(blocked).toContainText("bank account changed");
    } else {
      // Both findings are on one bill: Helm never plans to pay a probable duplicate, so only the
      // other bill is proposed, and the Guard passes it.
      await expect(pays).toHaveCount(1);
      await expect(pays.first()).toContainText("Guard passed · needs approval");
      await pays.first().getByRole("button", { name: "Why, and what the Guard checked" }).click();
      await expect(pays.first()).toContainText(`Due ${duplicate === "INV-E2E-1002" ? "15" : "20"} Oct 2026`);
    }

    // Helm proposes holding the probable duplicate; a person approves.
    const hold = rows.filter({ hasText: `Hold ${duplicate}: probable duplicate` }).first();
    await expect(hold).toContainText("Guard passed · needs approval");
    const bookId = new URL(page.url()).searchParams.get("book") ?? (await page.evaluate(() => {
      for (let i = 0; i < localStorage.length; i++) {
        const key = localStorage.key(i) ?? "";
        if (key.startsWith("helm.book.")) return localStorage.getItem(key);
      }
      return null;
    }));
    expect(bookId).toBeTruthy();
    const approved = page.waitForResponse((r) => /\/helm\/actions\/[^/]+\/approve$/.test(r.url()));
    await hold.getByRole("button", { name: /^Approve: / }).click();
    const response = await approved;
    expect(response.status()).toBe(200);
    const actionId = ((await response.json()) as Action).id;

    // The worker runs it; the Guard checks it again first.
    await expect
      .poll(async () => (await actions(bookId as string)).find((a) => a.id === actionId)?.state, { timeout: 60_000 })
      .toBe("EXECUTED");

    // Done recently, undoable, with a certificate whose fingerprint verifies.
    await page.reload();
    await settle(page);
    const recent = page.getByRole("region", { name: "Done recently" }).getByTestId("helm-action").filter({ hasText: `Hold ${duplicate}: probable duplicate` }).first();
    await expect(recent).toContainText("Done");
    await expect(recent.getByRole("button", { name: "Undo" })).toBeVisible();
    await recent.getByRole("button", { name: "Why, and what the Guard checked" }).click();
    await recent.getByRole("link", { name: "Read the certificate" }).click();
    await expect(page.getByText("(matches: unchanged since it was issued)")).toBeVisible();

    // Nothing is scheduled to pay: the payment run says so, and that Helm never moves money.
    await page.getByRole("navigation", { name: "Helm" }).getByRole("link", { name: "Plan" }).click();
    await expect(page.getByRole("heading", { name: "Plan", level: 1 })).toBeVisible();
    await expect(page.getByText("Nothing scheduled.", { exact: false })).toBeVisible();

    // Back to Documents: the switch remembers where the person was.
    await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Documents" }).click();
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/${customer.wsSlug}$`));
  });

  test("a member sees no mode switch and the API refuses them Helm", async ({ page, problems }) => {
    test.skip(process.env.E2E_LLM !== "1", NEEDS_MODEL);
    const member = await addMember(customer, "helm-member");
    const refused = await api(member.session, "GET", `/organizations/${customer.orgId}/helm`);
    expect(refused.status, refused.text.slice(0, 200)).toBe(403);
    expect(refused.text).toContain("HELM_ROLE");

    problems.allowHttp(/\/helm$/, [403], "a member is refused Helm; the page says where their Helm work is");
    await signInAs(page, member.email);
    await page.goto(customer.ws());
    await settle(page);
    await expect(page.getByRole("radiogroup", { name: "Mode" })).toHaveCount(0);
    await page.goto(`/${customer.orgSlug}/helm`);
    await expect(page.getByRole("heading", { name: "Helm is for owners, admins and billing" })).toBeVisible();
    await expect(page.getByRole("link", { name: "Open the review hub" })).toBeVisible();
  });
});
