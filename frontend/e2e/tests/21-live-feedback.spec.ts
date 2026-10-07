/**
 * LIVE FEEDBACK: what a person sees while background work runs, on archived access, and when an
 * image cannot be shown.
 *
 * F-141: "Reindex knowledge base" answered 202 and the page said nothing more; the button stayed
 *        enabled and nothing said whether re-embedding was running, finished or failed.
 * F-142: "Your workspace access" listed a workspace of an archived organization like an active one
 *        and dropped grants on archived workspaces.
 * F-143: a profile picture or logo the browser could not decode showed a broken-image icon, and the
 *        profile page refetched the picture in a loop for as long as it was open.
 */
import zlib from "node:zlib";

import type { Page, Route } from "@playwright/test";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { TENANTS, runId, ws } from "../support/env";
import { api, loginAs, resolveWorkspaceId } from "../support/api";

/** A solid-colour PNG (RGB, no filtering), built by hand. */
function png(width: number, height: number, rgb: [number, number, number]): Buffer {
  const chunk = (type: string, data: Buffer): Buffer => {
    const body = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const length = Buffer.alloc(4);
    length.writeUInt32BE(data.length);
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(zlib.crc32(body) >>> 0);
    return Buffer.concat([length, body, crc]);
  };
  const header = Buffer.alloc(13);
  header.writeUInt32BE(width, 0);
  header.writeUInt32BE(height, 4);
  header[8] = 8;
  header[9] = 2;
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: width }, () => rgb).flat())]);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

/** Answer with bytes that claim to be a PNG and are not one. */
const corrupt = (route: Route) =>
  route.fulfill({ status: 200, contentType: "image/png", body: Buffer.from("this is not a png at all") });

async function openDocumentsSettings(page: Page): Promise<void> {
  await page.goto(ws("C", "settings"));
  await expect(async () => {
    await page.getByRole("button", { name: /^Documents Extraction/ }).click();
    await expect(page.getByRole("heading", { name: "Knowledge Base Reindexing" })).toBeVisible({ timeout: 3_000 });
  }).toPass({ timeout: 30_000 });
}

test.describe("Knowledge base reindex (F-141)", () => {
  test.use({ user: "C.owner" });

  test("the card shows a real run from start to finish and the button waits for it", async ({ page }) => {
    test.setTimeout(180_000);
    await openDocumentsSettings(page);
    const card = page.getByTestId("reindex-status");
    await expect(card).toBeVisible();

    // Hold the request a moment so the in-flight state is observable.
    await page.route("**/knowledge-base/reindex", async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 1_200));
      await route.continue();
    });
    await page.getByRole("button", { name: "Reindex knowledge base…" }).click();
    const start = page.getByRole("button", { name: "Start reindexing" });
    await start.click();
    const starting = page.getByRole("button", { name: "Starting…" });
    await expect(starting).toBeDisabled();
    await expect(page.getByText(/^Re-embedding \d+ documents? in the background\.$/)).toBeVisible();

    // The worker re-embeds every completed document; the end of THIS run is announced and the
    // card ends on its completion time.
    await expect(page.getByText(/^Reindex (complete|finished):/)).toBeVisible({ timeout: 120_000 });
    await expect(card).toHaveAttribute("data-state", "idle");
    await expect(card).toContainText("Last completed on");
    await expect(card).toContainText(/\d+ of \d+ documents? re-embedded/);
    await expect(page.getByRole("button", { name: "Reindex knowledge base…" })).toBeEnabled();
    await expectHealthyPage(page);
  });

  test("while a run is active the button is disabled, progress is shown, and its end is announced", async ({
    page,
  }) => {
    const requestedAt = new Date(Date.now() - 30_000).toISOString();
    let phase: "running" | "done" = "running";
    await page.route("**/knowledge-base/reindex/status", (route) =>
      route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(
          phase === "running"
            ? {
                state: "running",
                active_jobs: 3,
                latest: { requested_at: requestedAt, finished_at: null, total: 5, waiting: 3, completed: 2, failed: 0 },
                last_completed_at: null,
              }
            : {
                state: "idle",
                active_jobs: 0,
                latest: {
                  requested_at: requestedAt,
                  finished_at: new Date().toISOString(),
                  total: 5,
                  waiting: 0,
                  completed: 5,
                  failed: 0,
                },
                last_completed_at: new Date().toISOString(),
              },
        ),
      }),
    );
    await openDocumentsSettings(page);

    const card = page.getByTestId("reindex-status");
    await expect(card).toHaveAttribute("data-state", "running");
    await expect(card).toContainText("Re-embedding in progress…");
    await expect(card).toContainText("2 of 5 documents");
    await expect(card.getByRole("progressbar", { name: "Reindex progress" })).toHaveAttribute("aria-valuenow", "40");
    const button = page.getByRole("button", { name: "Reindexing…" });
    await expect(button).toBeDisabled();

    phase = "done";
    await expect(page.getByText("Reindex complete: 5 documents re-embedded.")).toBeVisible({ timeout: 15_000 });
    await expect(card).toHaveAttribute("data-state", "idle");
    await expect(page.getByRole("button", { name: "Reindex knowledge base…" })).toBeEnabled();
  });
});

test.describe("Your workspace access (F-142)", () => {
  test.use({ user: "C.owner" });

  test("an archived organization's and an archived workspace's grants carry an ARCHIVED badge", async ({ page }) => {
    const session = await loginAs("C.owner");
    const { organizationId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);

    // An archived workspace in the shared organization (the creator holds an explicit grant).
    const workspaceName = `Grant drill ${runId()}`;
    const workspace = await api<{ id: string }>(session, "POST", `/organizations/${organizationId}/workspaces`, {
      workspace_name: workspaceName,
    });
    expect(workspace.status, workspace.text).toBeLessThan(300);
    expect((await api(session, "POST", `/workspaces/${workspace.body.id}/archive`)).status).toBe(200);

    // A whole organization archived, its first workspace untouched.
    const slug = `grant-org-${runId()}`.toLowerCase().slice(0, 40);
    const orgName = `Grant Org ${runId()}`;
    const created = await api<{ id: string; slug: string }>(session, "POST", "/organizations", {
      organization_name: orgName,
      organization_slug: slug,
    });
    expect(created.status, created.text).toBeLessThan(300);
    const archived = await api(session, "POST", `/organizations/${created.body.id}/archive`, {
      confirm_slug: created.body.slug,
    });
    expect(archived.status, archived.text).toBe(200);

    await page.goto(ws("C", "settings"));
    const panel = page.locator(".fp-card").filter({ has: page.getByRole("heading", { name: "Your workspace access" }) });
    await expect(panel).toBeVisible();

    const archivedWorkspace = panel.getByTestId("workspace-grant").filter({ hasText: workspaceName });
    await expect(archivedWorkspace).toHaveAttribute("data-archived", "true");
    await expect(archivedWorkspace.getByText("Archived", { exact: true })).toBeVisible();

    const inArchivedOrg = panel.getByTestId("workspace-grant").filter({ hasText: orgName });
    await expect(inArchivedOrg.first()).toHaveAttribute("data-archived", "true");
    await expect(inArchivedOrg.first().getByText("Archived", { exact: true })).toBeVisible();

    const active = panel.getByTestId("workspace-grant").filter({ hasText: "Caretakers Global Inc" });
    await expect(active.first()).toHaveAttribute("data-archived", "false");
    await expect(active.first().getByText("Archived", { exact: true })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});

test.describe("Images that cannot be shown (F-143)", () => {
  test.use({ user: "C.member" });

  test("a profile picture that cannot be decoded shows initials and is not refetched in a loop", async ({ page }) => {
    const session = await loginAs("C.member");
    const form = new FormData();
    form.append("file", new Blob([new Uint8Array(png(64, 64, [220, 38, 38]))], { type: "image/png" }), "a.png");
    const uploaded = await api(session, "POST", "/me/avatar", form);
    expect(uploaded.status, uploaded.text).toBeLessThan(300);

    let avatarRequests = 0;
    await page.route("**/users/*/avatar*", (route) => {
      avatarRequests += 1;
      return corrupt(route);
    });
    try {
      await page.goto(ws("C", "settings"));
      const account = page.getByRole("button", { name: /^Account menu for / });
      await expect(account).toBeVisible();
      await settle(page, 1_500);

      // Initials in place of the picture; no broken image anywhere on the page.
      await expect(account.locator("img")).toHaveCount(0);
      await expect(account.getByRole("img", { name: / avatar$/ })).toHaveText(/^[A-Z?]{1,2}$/);
      const broken = await page
        .locator("img")
        .evaluateAll((images: HTMLImageElement[]) => images.filter((img) => img.complete && img.naturalWidth === 0).length);
      expect(broken, "a broken-image icon is on screen").toBe(0);

      // The profile section's own preview fell back too, and stopped asking.
      const settled = avatarRequests;
      await page.waitForTimeout(2_500);
      expect(avatarRequests - settled, "the picture is being refetched in a loop").toBeLessThanOrEqual(1);
      await expectHealthyPage(page);
    } finally {
      await page.unroute("**/users/*/avatar*");
      await api(session, "DELETE", "/me/avatar");
    }
  });
});

test.describe("Logos that cannot be shown (F-143)", () => {
  test.use({ user: "C.owner" });

  test("a workspace logo that cannot be decoded shows the initials badge, in the header and in settings", async ({
    page,
  }) => {
    const owner = await loginAs("C.owner");
    const { organizationId } = await resolveWorkspaceId(owner, TENANTS.C.org, TENANTS.C.ws);
    const name = `Logo Drill ${runId()}`;
    const created = await api<{ id: string; slug: string }>(owner, "POST", `/organizations/${organizationId}/workspaces`, {
      workspace_name: name,
    });
    expect(created.status, created.text).toBeLessThan(300);
    const workspaceId = created.body.id;
    const form = new FormData();
    form.append("file", new Blob([new Uint8Array(png(128, 128, [37, 99, 235]))], { type: "image/png" }), "logo.png");
    const logo = await api(owner, "POST", `/workspaces/${workspaceId}/upload/logo`, form);
    expect(logo.status, logo.text).toBeLessThan(300);

    const brokenImages = () =>
      page
        .locator("img")
        .evaluateAll((images: HTMLImageElement[]) => images.filter((img) => img.complete && img.naturalWidth === 0).length);

    await page.route("**/workspaces/*/logo*", corrupt);
    try {
      await page.goto(`/${TENANTS.C.org}/${created.body.slug}`);
      await expect(page.locator("main")).toBeVisible();
      await settle(page, 1_500);
      expect(await brokenImages(), "a broken-image icon is in the header").toBe(0);
      await expect(page.getByText("LD", { exact: true }).first()).toBeVisible();

      await page.goto(`/${TENANTS.C.org}/${created.body.slug}/settings`);
      await expect(async () => {
        await page.getByRole("button", { name: /^General Name, locale and members/ }).click();
        await expect(page.getByText("Company Logo")).toBeVisible({ timeout: 3_000 });
      }).toPass({ timeout: 30_000 });
      await settle(page, 1_500);
      expect(await brokenImages(), "a broken-image icon is in the logo preview").toBe(0);
      await expect(page.getByText("Unavailable", { exact: true })).toBeVisible();
      await expectHealthyPage(page);
    } finally {
      await page.unroute("**/workspaces/*/logo*");
      await api(owner, "POST", `/workspaces/${workspaceId}/archive`);
    }
  });
});

test.describe("A page that cannot load (F-144)", () => {
  test.use({ user: "C.owner" });

  test("a deploy while the tab is open shows a reload card inside the shell, and other pages still work", async ({
    page,
    problems,
  }) => {
    const chunk = /\/assets\/Cases-[\w-]+\.js$/;
    problems.allowHttp(chunk, [404], "simulates a deploy: the page's old code file is gone");
    problems.allowConsole(/dynamically imported module|The above error occurred/i, "the missing page code");

    await page.goto(ws("C"));
    await expect(page.locator("main")).toContainText("Recent Activity");
    await page.route(chunk, (route) => route.fulfill({ status: 404, contentType: "text/plain", body: "gone" }));
    await page.getByRole("link", { name: /^Cases/ }).first().click();

    // One automatic reload is tried; the code is still missing, so the card explains it.
    await expect(page.getByRole("heading", { name: "A new version of FlowPilot is available" })).toBeVisible({
      timeout: 30_000,
    });
    await expect(page.getByRole("button", { name: "Reload page" })).toBeVisible();
    // The sidebar and header are still there, and another page opens normally.
    await expect(page.getByRole("button", { name: /^Account menu for / })).toBeVisible();
    await page.unroute(chunk);
    await page.getByRole("link", { name: "Documents", exact: true }).click();
    await expect(page.getByRole("heading", { name: "A new version of FlowPilot is available" })).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
