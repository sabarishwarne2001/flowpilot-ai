/**
 * HELM, Night Bridge (the Helm visual session): the new look's behaviour, not its pixels.
 *
 * 1. Before a book is chosen, Helm's pages are closed: choosing one in the sidebar explains why and
 *    stays on Start, instead of navigating there and being sent straight back (the "blink").
 * 2. Switching Documents → Helm → Documents with motion on runs a view transition: the
 *    data-helm-transition attribute is set while it runs and removed after, the address and the
 *    remembered page are right, and focus stays on the switch.
 * 3. Under reduced motion, document.startViewTransition is never called and the switch still
 *    navigates.
 * 4. The cockpit's KPI strip, Promise calendar and fan chart show the sample company's real values,
 *    and each has its screen-reader alternative; a count-up ends on exactly the API's value.
 * 5. Keyboard: the switch's arrow keys; the calendar's arrows, and Enter opening the ledger on that day.
 * 6. Performance: the cockpit shows its data within 1.5 s of navigation, and no long task over
 *    200 ms comes from the entrance animations that follow.
 */
import type { Page } from "@playwright/test";

import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

let customer: Customer;
let bookId: string;

const base = (): string => `/organizations/${customer.orgId}/helm`;

/** Counts startViewTransition calls and records every value data-helm-transition takes. */
async function watchTransitions(page: Page): Promise<void> {
  await page.addInitScript(() => {
    const record = { calls: 0, values: [] as (string | null)[] };
    (window as unknown as { __vt: typeof record }).__vt = record;
    type Start = (...args: unknown[]) => unknown;
    const proto = Document.prototype as unknown as { startViewTransition?: Start };
    const original = proto.startViewTransition;
    if (original) {
      proto.startViewTransition = function counted(this: Document, ...args: unknown[]) {
        record.calls += 1;
        return original.apply(this, args);
      };
    }
    const observe = (): void => {
      new MutationObserver(() => record.values.push(document.documentElement.getAttribute("data-helm-transition")))
        .observe(document.documentElement, { attributes: true, attributeFilter: ["data-helm-transition"] });
    };
    if (document.documentElement) observe();
    else document.addEventListener("DOMContentLoaded", observe);
  });
}

const transitions = (page: Page) =>
  page.evaluate(() => (window as unknown as { __vt: { calls: number; values: (string | null)[] } }).__vt);

const inr = (micros: number): string =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", maximumFractionDigits: 2 }).format(micros / 1_000_000);
const inrCompact = (micros: number): string =>
  new Intl.NumberFormat("en-IN", { style: "currency", currency: "INR", notation: "compact", maximumFractionDigits: 1 }).format(
    micros / 1_000_000,
  );

test.describe("Helm Night Bridge — before a book is chosen", () => {
  test("the sidebar's pages explain they need a book, and Start does not blink", async ({ page }) => {
    const fresh = await newCustomer("helmnobook", "No Book Yet Ltd");
    await signInAs(page, fresh.email);
    await page.goto(`/${fresh.orgSlug}/helm/start`);
    await expect(page.getByRole("heading", { name: "Helm keeps your business promises", level: 1 })).toBeVisible();
    await settle(page);
    const nav = page.getByRole("navigation", { name: "Helm" });
    const plan = nav.getByRole("link", { name: "Plan" });
    await expect(plan).toHaveAttribute("aria-disabled", "true");
    const before = await page.evaluate(() => window.history.length);
    // Playwright does not click an aria-disabled element by itself; a person still can.
    await plan.click({ force: true });
    await expect(page.getByTestId("helm-nav-closed")).toContainText("Open the sample company or start Helm on a workspace");
    await expect(page).toHaveURL(new RegExp(`/${fresh.orgSlug}/helm/start$`));
    // The old sidebar pushed /helm/plan and was redirected back: one more history entry, a blink.
    expect(await page.evaluate(() => window.history.length)).toBe(before);
    // The keyboard gets the same explanation.
    await nav.getByRole("link", { name: "Time machine" }).focus();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/${fresh.orgSlug}/helm/start$`));
  });
});

test.describe("Helm Night Bridge — the sample company", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(async () => {
    test.setTimeout(420_000);
    customer = await newCustomer("helmnight", "Night Bridge Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "3");
    const built = await api(customer.session, "POST", `${base()}/sample`);
    expect(built.status, built.text.slice(0, 300)).toBe(202);
    await expect
      .poll(async () => {
        const overview = await api<{ sample: { book_id: string | null; building: boolean } }>(customer.session, "GET", base());
        bookId = overview.body.sample?.book_id ?? "";
        return Boolean(bookId) && !overview.body.sample.building;
      }, { timeout: 300_000, intervals: [3_000, 5_000] })
      .toBe(true);
  });

  test("switching to Helm and back runs a view transition, remembers the page and keeps focus on the switch", async ({ page }) => {
    await watchTransitions(page);
    await signInAs(page, customer.email);
    await page.goto(customer.ws());
    await settle(page);
    const documentsHere = new URL(page.url()).pathname;

    await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ }).click();
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/helm(\\?|$)`));
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
    await expect.poll(async () => (await transitions(page)).values).toContain("enter");
    await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute("data-helm-transition"))).toBeNull();
    expect((await transitions(page)).calls).toBeGreaterThan(0);
    await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ })).toBeFocused();
    const remembered = await page.evaluate((id) => window.localStorage.getItem(`helm.path.${id}.helm`), customer.orgId);
    expect(remembered).toMatch(new RegExp(`^/${customer.orgSlug}/helm`));

    await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Documents" }).click();
    await expect(page).toHaveURL(new RegExp(`${documentsHere}$`));
    await expect.poll(async () => (await transitions(page)).values).toContain("leave");
    await expect.poll(() => page.evaluate(() => document.documentElement.getAttribute("data-helm-transition"))).toBeNull();
    await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Documents" })).toBeFocused();
  });

  test.describe("under reduced motion", () => {
    test("no view transition is started, and the switch still navigates", async ({ page }) => {
      await page.emulateMedia({ reducedMotion: "reduce" });
      await watchTransitions(page);
      await signInAs(page, customer.email);
      await page.goto(customer.ws());
      await settle(page);
      expect(await page.evaluate(() => window.matchMedia("(prefers-reduced-motion: reduce)").matches)).toBe(true);
      await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ }).click();
      await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
      await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Documents" }).click();
      await expect(page).toHaveURL(new RegExp(`${customer.ws()}$`));
      const seen = await transitions(page);
      expect(seen.calls).toBe(0);
      expect(seen.values.filter(Boolean)).toEqual([]);
    });
  });

  test("the cockpit shows the sample company's real figures, each with a screen-reader alternative", async ({ page }) => {
    const cockpit = await api<{
      summary: {
        instruments: { state: string }[];
        counts: { actions: number; needs_approval: number; payments: number };
        payments_by_currency: Record<string, number>;
      };
    }>(customer.session, "GET", `${base()}/books/${bookId}/cockpit`);
    expect(cockpit.status).toBe(200);
    const calendar = await api<{ entries: { direction: string; on: string; currency: string; amount_micros: number; count: number }[] }>(
      customer.session, "GET", `${base()}/books/${bookId}/calendar`,
    );
    expect(calendar.status).toBe(200);
    const { instruments, counts, payments_by_currency: payments } = cockpit.body.summary;

    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm?book=${bookId}`);
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();

    // KPI strip: the figures the API gave, in words for screen readers too.
    const holding = instruments.filter((i) => i.state === "holding").length;
    const kpis = page.getByTestId("helm-kpis");
    await expect(page.getByTestId("helm-kpi-holding-value")).toHaveText(`${holding}/ ${instruments.length}`);
    await expect(page.getByTestId("helm-kpi-holding")).toContainText(`${holding} / ${instruments.length}`);
    await expect(page.getByTestId("helm-kpi-approval-value")).toHaveText(`${counts.needs_approval}/ ${counts.actions}`);
    await expect(kpis.locator(".sr-only").first()).toHaveText(`${holding} / ${instruments.length}`);

    // A count-up ends on exactly the API's value.
    const planned = payments.INR ?? 0;
    const value = page.getByTestId("helm-kpi-payments-value");
    await expect(value).toHaveAttribute("data-value", inrCompact(planned));
    await expect(value).toHaveText(inrCompact(planned));
    await expect(page.getByTestId("helm-kpi-payments")).toContainText(inr(planned));

    // The Promise calendar: the first day we owe something, by its amount, and its summary in words.
    const owe = calendar.body.entries.filter((e) => e.direction === "WE_OWE" && e.currency === "INR");
    expect(owe.length, "the sample company owes money in the next 12 weeks").toBeGreaterThan(0);
    const day = owe[0] as (typeof owe)[number];
    const grid = page.getByRole("grid", { name: /^Promise calendar: what we owe in INR/ });
    await expect(grid).toBeVisible();
    await expect(grid.locator(`[data-date="${day.on}"]`)).toHaveAttribute("aria-label", new RegExp(inr(day.amount_micros).replace(/[.$]/g, "\\$&")));
    await expect(page.getByTestId("helm-calendar-summary")).toContainText("Over the next 12 weeks");

    // The fan chart: its summary as the image's name, and the forecast as a table.
    await expect(page.getByRole("img", { name: /^Cash in INR over the next \d+ days/ })).toBeVisible();
    await page.getByText("Show the forecast as a table").click();
    await expect(page.getByRole("columnheader", { name: "Middle (P50)" })).toBeVisible();
    // Payments by week, as a table for screen readers.
    await expect(page.getByTestId("helm-weeks").locator("table caption")).toContainText("The plan's payments in INR");
  });

  test("the keyboard drives the switch and the calendar, and Enter opens the ledger on that day", async ({ page }) => {
    const calendar = await api<{ entries: { direction: string; on: string; currency: string }[] }>(
      customer.session, "GET", `${base()}/books/${bookId}/calendar`,
    );
    await signInAs(page, customer.email);
    await page.goto(`/${customer.orgSlug}/helm?book=${bookId}`);
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
    await settle(page);

    // The calendar: one tab stop on today; Right moves a week, Down a day; Enter opens that day.
    const grid = page.getByRole("grid", { name: /^Promise calendar/ });
    const start = grid.locator('[tabindex="0"]');
    await start.focus();
    const today = await start.getAttribute("data-date");
    expect(today).toBeTruthy();
    await page.keyboard.press("ArrowRight");
    const week = new Date(Date.parse(`${today}T00:00:00Z`) + 7 * 86_400_000).toISOString().slice(0, 10);
    await expect(grid.locator(`[data-date="${week}"]`)).toBeFocused();
    await page.keyboard.press("ArrowDown");
    const next = new Date(Date.parse(`${week}T00:00:00Z`) + 86_400_000).toISOString().slice(0, 10);
    await expect(grid.locator(`[data-date="${next}"]`)).toBeFocused();
    await page.keyboard.press("Enter");
    await expect(page).toHaveURL(new RegExp(`/helm/ledger\\?.*view=owe&due=${next}`));
    await expect(page.getByTestId("helm-ledger-day")).toBeVisible();
    const due = (calendar.body.entries ?? []).filter((e) => e.direction === "WE_OWE" && e.on === next).length;
    if (due === 0) await expect(page.getByText("Nothing here.")).toBeVisible();
    else await expect(page.getByTestId("helm-promise").first()).toBeVisible();

    // The switch: one tab stop; ArrowLeft goes to Documents and focus follows.
    await page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ }).focus();
    await page.keyboard.press("ArrowLeft");
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/(?!helm)`));
    await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: "Documents" })).toBeFocused();
    await page.keyboard.press("End");
    await expect(page).toHaveURL(new RegExp(`/${customer.orgSlug}/helm`));
    await expect(page.getByRole("radiogroup", { name: "Mode" }).getByRole("radio", { name: /^Helm/ })).toBeFocused();
  });

  test("the cockpit is ready within 1.5 s, and its entrance makes no long task over 200 ms", async ({ page }) => {
    await page.addInitScript(() => {
      const tasks: { start: number; duration: number }[] = [];
      (window as unknown as { __longTasks: typeof tasks }).__longTasks = tasks;
      try {
        new PerformanceObserver((list) => {
          for (const entry of list.getEntries()) tasks.push({ start: entry.startTime, duration: entry.duration });
        }).observe({ type: "longtask", buffered: true });
      } catch {
        // No long-task timing in this browser: nothing to record.
      }
    });
    await signInAs(page, customer.email);
    // A warm visit first (fonts and code cached), as a person switching to Helm would have.
    await page.goto(`/${customer.orgSlug}/helm?book=${bookId}`);
    await expect(page.getByRole("heading", { name: "Cockpit", level: 1 })).toBeVisible();
    await settle(page);
    await page.reload();
    await expect(page.getByTestId("helm-kpis")).toBeVisible();
    const ready = await page.waitForFunction(() => performance.getEntriesByName("helm:cockpit-ready")[0]?.startTime ?? null);
    const readyAt = (await ready.jsonValue()) as number;
    expect(readyAt, "milliseconds from navigation to the cockpit's data on screen").toBeLessThan(1_500);
    await page.waitForTimeout(1_500);
    const long = await page.evaluate(
      (from) =>
        (window as unknown as { __longTasks: { start: number; duration: number }[] }).__longTasks.filter(
          (t) => t.start >= from && t.duration > 200,
        ),
      readyAt,
    );
    expect(long, "long tasks during the cockpit's entrance").toEqual([]);
  });
});
