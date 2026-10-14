/**
 * JOURNEY 7 — an organization is archived and restored; nothing runs while it is archived
 * (campaign session 4, §D; F-134).
 *
 * A Developer customer works through an API key. The owner archives the organization from its
 * settings (typing its slug to confirm): it is listed apart, its API key and its people are
 * refused, and background work queued for it is skipped by the worker rather than run. The owner
 * restores it, and the same key works again with every document still there.
 */
import fs from "node:fs";
import path from "node:path";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api } from "../support/api";
import { API_BASE } from "../support/env";
import { documents, invoicePdf, newCustomer, signInAs, standIn } from "../support/journey";

async function uploadWithKey(key: string, workspaceId: string, label: string): Promise<number> {
  const file = invoicePdf(label);
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
  const response = await fetch(`${API_BASE}/workspaces/${workspaceId}/work-items`, {
    method: "POST",
    headers: { Authorization: `Bearer ${key}` },
    body: form,
  });
  return response.status;
}

test.describe("Journey: archive and restore an organization", () => {
  test.setTimeout(300_000);

  test("archived: listed apart, refused, nothing runs; restored: everything as it was", async ({ page, problems }) => {
    const customer = await newCustomer("archive-journey", "Archive Journey Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "developer", "--seats", "1");
    await signInAs(page, customer.email);

    // An API key, made in the browser and shown once.
    await page.goto(customer.org("api-keys"));
    await page.getByRole("button", { name: "New key" }).click();
    await page.getByLabel("Name").fill("Nightly import");
    for (const scope of ["workspaces:read", "work_items:read", "work_items:write"]) {
      await page.getByRole("checkbox", { name: scope }).check();
    }
    await page.getByRole("button", { name: "Create key" }).click();
    const revealed = page.getByRole("region", { name: "New API key" });
    await expect(revealed).toBeVisible();
    const key = (await revealed.locator("code").innerText()).trim();
    expect(key.length).toBeGreaterThan(20);
    await revealed.getByRole("button", { name: "I've saved it" }).click();
    expect(await uploadWithKey(key, customer.wsId, "archive-before")).toBe(201);
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(1);

    // The owner archives it from the settings, typing the slug to confirm. A plan that renews would
    // keep charging, so it must be cancelled first (F-254); the page says so.
    problems.allowHttp(/\/organizations\/[^/]+\/archive$/, [409], "a renewing subscription must be cancelled first");
    await page.goto(customer.org("settings"));
    await page.getByRole("button", { name: "Archive organization" }).click();
    await page.getByLabel(/to confirm/).fill(customer.orgSlug);
    await page.getByRole("button", { name: "Archive organization" }).click();
    await expect(page.getByText(/still has a paid subscription that renews/)).toBeVisible();
    standIn("cancel-at-period-end", customer.orgSlug);  // cancelled in the billing portal
    await page.getByRole("button", { name: "Archive organization" }).click();
    await page.getByLabel(/to confirm/).fill(customer.orgSlug);
    await page.getByRole("button", { name: "Archive organization" }).click();
    await expect(page).toHaveURL(/\/workspaces/, { timeout: 15_000 });
    await page.goto("/workspaces");
    const archived = page.getByRole("region", { name: "Archived organizations" })
      .getByRole("article").filter({ hasText: "Archive Journey Ltd" });
    await expect(archived).toBeVisible({ timeout: 15_000 });

    // Archived: the key is refused, and work queued for it is skipped by the worker.
    expect(await uploadWithKey(key, customer.wsId, "archive-during")).toBeGreaterThanOrEqual(400);
    const skipped = standIn("run-job", customer.orgSlug, "--job", "knowledge.reindex") as {
      status?: string; result?: { outcome?: string; reason?: string };
    };
    expect(skipped.status, JSON.stringify(skipped)).toBe("SUCCEEDED");
    expect(skipped.result?.outcome).toBe("SKIPPED");
    expect(skipped.result?.reason).toContain("archived");

    // Restored: nothing was lost. Keys the archive switched off stay off (a leaked key from before
    // must not come back to life); a new one works.
    await archived.getByRole("button", { name: "Restore organization" }).click();
    await archived.getByRole("textbox", { name: "Type the organization's slug to confirm" }).fill(customer.orgSlug);
    await archived.getByRole("button", { name: "Restore", exact: true }).click();
    await expect(page.getByText("Archive Journey Ltd restored.")).toBeVisible();
    expect(await uploadWithKey(key, customer.wsId, "archive-old-key")).toBe(401);
    const fresh = await api<{ token?: string; key?: string }>(customer.session, "POST",
      `/organizations/${customer.orgId}/api-keys`, { name: "After restore", scopes: ["workspaces:read", "work_items:write"] });
    expect(fresh.status, fresh.text).toBe(201);
    expect(await uploadWithKey(String(fresh.body.token ?? fresh.body.key), customer.wsId, "archive-after")).toBe(201);
    await expect.poll(async () => (await documents(customer)).length, { timeout: 60_000 }).toBe(2);
    await page.goto(customer.ws("work-items"));
    await expect(page.getByRole("table").getByText(/archive-before/)).toBeVisible({ timeout: 15_000 });
    await expectHealthyPage(page);
  });
});
