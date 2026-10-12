/**
 * Campaign session 3 (coverage ledger) — organization consoles, each change followed to its effect.
 *
 * `20-organization` walks every organization page; several of its steps only click, or accept any
 * outcome. Here each change is followed to where it should show: the server (read back through
 * the API), a reload of the page, and for an API key the public gateway itself. Every test puts
 * back what it changed, because the other specs share tenant C.
 */
import type { Page } from "@playwright/test";

import { test, expect, settle } from "../support/fixtures";
import { api, resolveWorkspaceId, type ApiSession } from "../support/api";
import { TENANTS, org, runId } from "../support/env";

test.use({ user: "C.owner" });

const ids = (session: ApiSession | null) => {
  if (!session) {
    throw new Error("these tests need the signed-in owner's session");
  }
  return resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws).then((found) => ({ ...found, session }));
};

test.describe("Developer platform", () => {
  test("an issued key works at the gateway, and a new tier is what the gateway enforces", async ({ page, session }) => {
    const { organizationId, workspaceId, session: owner } = await ids(session);
    const name = `E2E effect ${runId()}`;
    await page.goto(org("C", "developer"));
    await page.getByRole("button", { name: "Issue key" }).first().click();
    await page.getByRole("textbox", { name: "Name" }).fill(name);
    await page.getByRole("button", { name: "Issue key" }).last().click();
    await expect(page.getByText(/only time this token is shown/i)).toBeVisible({ timeout: 15_000 });
    const token = (await page.locator("code.break-all").first().innerText()).trim();
    await page.getByRole("button", { name: "I have saved it" }).click();

    const gateway = async () =>
      api<{ rate_limit: { tier: string; limit: number } }>(
        null, "GET", `/public/documents?workspace_id=${workspaceId}`, undefined, { Authorization: `Bearer ${token}` },
      );
    const before = await gateway();
    expect(before.status, before.text).toBe(200);
    expect(before.body.rate_limit.tier).toBe("FREE");

    const row = page.locator("li").filter({ hasText: name });
    await expect(row).toBeVisible();
    await row.getByRole("combobox").selectOption("BUILDER");
    const saved = page.waitForResponse((response) => response.request().method() === "PATCH" && /\/tier$/.test(response.url()));
    await row.getByRole("button", { name: "Save tier" }).click();
    expect((await saved).status()).toBe(200);
    await expect(row.getByRole("button", { name: /^(Save tier|Saving…)$/ })).toBeHidden({ timeout: 10_000 });
    const after = await gateway();
    expect(after.body.rate_limit.tier).toBe("BUILDER");

    await page.reload();
    await expect(page.locator("li").filter({ hasText: name }).getByRole("combobox")).toHaveValue("BUILDER");

    const keys = await api<{ keys: Array<{ id: string; name: string }> }>(owner, "GET", `/organizations/${organizationId}/developer`);
    const mine = keys.body.keys.find((key) => key.name === name);
    expect(mine).toBeTruthy();
    await api(owner, "DELETE", `/organizations/${organizationId}/api-keys/${mine!.id}`);
    expect((await gateway()).status).toBe(401);
  });
});

test.describe("Service levels", () => {
  test("an organization target is saved, shown after a reload, and reset to the default", async ({ page, session }) => {
    const { organizationId, session: owner } = await ids(session);
    await page.goto(org("C", "service-levels"));
    const card = page.locator("li").filter({ has: page.getByRole("heading", { name: "API availability" }) });
    await card.getByRole("button", { name: "Change target" }).click();
    await card.getByRole("textbox", { name: "Target for API availability" }).fill("0.995");
    await card.getByRole("button", { name: "Save" }).click();
    await expect(card).toContainText(/target 99\.50?\s?%/, { timeout: 10_000 });

    const read = async () => {
      const response = await api<{ entries: Array<{ target: { display_name: string; target_value: string; source: string } }> }>(
        owner, "GET", `/organizations/${organizationId}/slos`,
      );
      return response.body.entries.find((row) => row.target.display_name === "API availability")!.target;
    };
    expect(Number((await read()).target_value)).toBeCloseTo(0.995);
    expect((await read()).source).toBe("ORGANIZATION");

    await page.reload();
    const again = page.locator("li").filter({ has: page.getByRole("heading", { name: "API availability" }) });
    await expect(again).toContainText(/target 99\.50?\s?%/);
    await again.getByRole("button", { name: "Change target" }).click();
    await again.getByRole("button", { name: "Use platform default" }).click();
    await expect(page.locator("body")).toContainText(/reset to the platform default/i, { timeout: 10_000 });
    expect((await read()).source).not.toBe("ORGANIZATION");
  });
});

const openAnalyticsTab = async (page: Page, tab: string): Promise<void> => {
  await page.getByRole("tab", { name: tab, exact: true }).click();
  await settle(page, 300);
};

test.describe("Analytics and BI egress", () => {
  test("a destination and a schedule are created, paused and removed", async ({ page, session }) => {
    const { organizationId, session: owner } = await ids(session);
    const label = `E2E bucket ${runId()}`;
    await page.goto(org("C", "analytics"));
    await page.getByRole("button", { name: "Add destination" }).first().click();
    await page.getByRole("textbox", { name: "Label" }).fill(label);
    await page.getByRole("textbox", { name: "Bucket" }).fill("caretakers-e2e-bucket");
    await page.getByRole("textbox", { name: "Region" }).fill("eu-west-2");
    await page.getByRole("textbox", { name: "Key prefix" }).fill("flowpilot/");
    await page.getByRole("textbox", { name: "Access key ID" }).fill("AKIAE2ETESTONLY0000");
    await page.getByRole("textbox", { name: "Secret access key" }).fill("e2e-secret-not-real");
    await page.getByRole("button", { name: "Add destination" }).last().click();
    await expect(page.locator("main")).toContainText(label, { timeout: 20_000 });

    const destinations = await api<Array<{ id: string; label: string }>>(owner, "GET", `/organizations/${organizationId}/analytics/destinations`);
    const destination = destinations.body.find((row) => row.label === label);
    expect(destination, destinations.text).toBeTruthy();

    try {
      await openAnalyticsTab(page, "Sync schedules");
      await page.locator("#schedule-destination").selectOption(destination!.id);
      const box = page.getByRole("checkbox").first();
      if (!(await box.isChecked())) {
        await box.check();
      }
      await page.getByRole("button", { name: "Create schedule" }).click();
      const scheduleRow = page.locator("div").filter({ hasText: label }).filter({ has: page.getByRole("button", { name: "Pause" }) }).last();
      await expect(scheduleRow).toBeVisible({ timeout: 15_000 });

      const schedules = async () =>
        (await api<Array<{ id: string; destination_id: string; enabled: boolean }>>(owner, "GET", `/organizations/${organizationId}/analytics/schedules`))
          .body.filter((row) => row.destination_id === destination!.id);
      expect((await schedules()).map((row) => row.enabled)).toEqual([true]);

      await scheduleRow.getByRole("button", { name: "Pause" }).click();
      await expect(page.locator("body")).toContainText(/schedule paused/i, { timeout: 10_000 });
      expect((await schedules()).map((row) => row.enabled)).toEqual([false]);

      await page.reload();
      await openAnalyticsTab(page, "Sync schedules");
      const paused = page.locator("div").filter({ hasText: label }).filter({ has: page.getByRole("button", { name: "Resume" }) }).last();
      await expect(paused).toBeVisible({ timeout: 15_000 });
      await paused.getByRole("button", { name: "Delete" }).click();
      await expect(page.locator("body")).toContainText(/schedule deleted/i, { timeout: 10_000 });
      expect(await schedules()).toEqual([]);
    } finally {
      await api(owner, "DELETE", `/organizations/${organizationId}/analytics/destinations/${destination!.id}`);
    }
  });
});

test.describe("Calibrated autonomy", () => {
  test("a new error limit is saved, kept after a reload, and put back", async ({ page }) => {
    await page.goto(org("C", "autonomy"));
    const slider = page.locator("#autonomy-alpha");
    await expect(slider).toBeVisible();
    const original = await slider.inputValue();
    const moved = String(Math.min(200, Number(original) + 10));
    await slider.focus();
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("ArrowRight");
    await expect(slider).toHaveValue(moved);
    await page.getByRole("button", { name: "Save and recalculate" }).click();
    await settle(page);

    await page.reload();
    await expect(page.locator("#autonomy-alpha")).toHaveValue(moved, { timeout: 15_000 });

    await page.locator("#autonomy-alpha").focus();
    await page.keyboard.press("ArrowLeft");
    await page.keyboard.press("ArrowLeft");
    await page.getByRole("button", { name: "Save and recalculate" }).click();
    await settle(page);
    await page.reload();
    await expect(page.locator("#autonomy-alpha")).toHaveValue(original, { timeout: 15_000 });
  });
});

test.describe("Enterprise BYOK and models", () => {
  test("a provider key is stored masked, tested with a stated result, and retired", async ({ page, problems }) => {
    problems.allowHttp(/\/byok/, [400, 422, 502, 503], "the provider cannot be reached from the sandbox");
    await page.goto(org("C", "byok"));
    // A provider with no key yet (an earlier run may have kept one), so nothing in use is touched.
    const free = page.locator("article").filter({ has: page.getByRole("button", { name: "Add key", exact: true }) }).first();
    const provider = (await free.locator("h3").first().innerText()).trim();
    const card = () => page.locator("article").filter({ has: page.getByRole("heading", { name: provider, exact: true }) }).first();

    await card().getByRole("button", { name: "Add key", exact: true }).click();
    // The key field is a password input (not a textbox); the key takes the provider's own prefix.
    const field = card().getByLabel(/API key$/);
    const prefix = ((await field.getAttribute("placeholder")) ?? "").replace("…", "").replace("API key", "");
    await field.fill(`${prefix}e2e-not-a-real-key-${runId()}WXYZ`);
    await card().getByRole("button", { name: "Save key" }).click();
    await expect(card()).toContainText("Key stored. Run a test to confirm it works.", { timeout: 15_000 });
    await expect(card()).toContainText("••••WXYZ");

    await page.reload();
    await expect(card()).toContainText("••••WXYZ", { timeout: 15_000 });
    await card().getByRole("button", { name: "Test connection" }).click();
    // A made-up key never connects: the card says so, in words, rather than going quiet.
    await expect(card()).toContainText(/rejected this key|could not|unreachable|failed|invalid|error/i, { timeout: 30_000 });
    await expect(card()).not.toContainText(/Connected in \d+ ms/);

    await card().getByRole("button", { name: "Retire" }).click();
    await expect(card()).toContainText("Key retired.", { timeout: 15_000 });
    await expect(card()).not.toContainText("••••WXYZ");
    await expect(card().getByRole("button", { name: "Add key", exact: true })).toBeVisible();
  });
});
