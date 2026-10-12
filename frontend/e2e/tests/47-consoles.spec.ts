/**
 * Campaign session 3 — the consoles people run the product from, as they are used.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org } from "../support/env";

test.describe("Audit log filters (F-311)", () => {
  test.use({ user: "C.owner" });

  // The filters were free text checked against exact codes: a lowercase or partial word showed
  // "Audit records could not be loaded", the actor filter needed an id the table never showed,
  // and Clear left the date box filled.
  test("an owner filters by action, by person and by date, and clears it all", async ({ page, session }) => {
    await page.goto(org("C", "audit"));
    const action = page.getByLabel("Action", { exact: true });
    await expect(action.locator("option").filter({ hasText: /^Updated$/ })).toHaveCount(1);
    await action.selectOption({ label: "Updated" });
    await expect(page.getByText(/could not be loaded/i)).toHaveCount(0);
    const actionCells = page.locator("tbody tr td:nth-child(3)");
    await expect(actionCells.first()).toBeVisible();
    for (const text of await actionCells.allInnerTexts()) {
      expect(text.trim()).toBe("Updated");
    }

    const actor = page.getByLabel("Actor", { exact: true });
    await actor.selectOption(String(session!.me.id));
    const me = (await actor.locator("option:checked").innerText()).trim();
    expect(me).not.toMatch(/^[0-9a-f-]{8,}$/);
    await expect(page.getByText(/could not be loaded/i)).toHaveCount(0);
    for (const text of await page.locator("tbody tr td:nth-child(2)").allInnerTexts()) {
      expect(text).toContain(me);
    }

    const from = page.getByLabel("From date", { exact: true });
    await from.fill("2026-01-01");
    await page.getByRole("button", { name: "Clear" }).click();
    await expect(from).toHaveValue("");
    await expect(action).toHaveValue("");
    await expect(page.getByRole("button", { name: "Clear" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
