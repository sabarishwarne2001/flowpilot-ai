/**
 * PROFILE PICTURE AND WORKSPACE LOGO: a change shows up at once, also after a reload.
 *
 * Both were served at a fixed address with `Cache-Control: private, max-age=300`. Within one
 * page a shared counter forces a refetch, but it lives in memory: after a reload (or in another
 * tab or device) the browser served its cached copy for up to five minutes, so a new picture
 * was replaced by the old one in the sidebar profile card and the workspace switcher.
 */
import zlib from "node:zlib";

import { test, expect, expectHealthyPage } from "../support/fixtures";
import { TENANTS, ws } from "../support/env";
import { api, loginAs, resolveWorkspaceId } from "../support/api";

test.use({ user: "C.member" });

/** A solid-colour PNG of the given size (RGB, no filtering), built by hand. */
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
  header[8] = 8; // bit depth
  header[9] = 2; // RGB
  const row = Buffer.concat([Buffer.from([0]), Buffer.from(Array.from({ length: width }, () => rgb).flat())]);
  const raw = Buffer.concat(Array.from({ length: height }, () => row));
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", header),
    chunk("IDAT", zlib.deflateSync(raw)),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

async function uploadAvatar(session: Awaited<ReturnType<typeof loginAs>>, image: Buffer): Promise<void> {
  const form = new FormData();
  form.append("file", new Blob([image], { type: "image/png" }), "avatar.png");
  const response = await api(session, "POST", "/me/avatar", form);
  expect(response.status, response.text).toBeLessThan(300);
}

const sidebarAvatarWidth = (page: import("@playwright/test").Page) =>
  page
    .getByRole("button", { name: /^Account menu for / })
    .locator("img")
    .evaluate((img: HTMLImageElement) => (img.complete ? img.naturalWidth : 0))
    .catch(() => 0);

test("a new profile picture replaces the old one after a reload", async ({ page }) => {
  const session = await loginAs("C.member");
  await uploadAvatar(session, png(64, 64, [220, 38, 38]));
  try {
    await page.goto(ws("C"));
    await expect.poll(() => sidebarAvatarWidth(page), { timeout: 15_000 }).toBe(64);

    // Changed elsewhere (another tab or device), then this page is reloaded.
    await uploadAvatar(session, png(96, 96, [37, 99, 235]));
    await page.reload();
    await expect.poll(() => sidebarAvatarWidth(page), { timeout: 15_000 }).toBe(96);
    await expectHealthyPage(page);
  } finally {
    await api(session, "DELETE", "/me/avatar");
  }
});

test("the workspace logo is revalidated, not served stale from the cache", async () => {
  const session = await loginAs("C.member");
  const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
  const response = await fetch(`http://127.0.0.1:8000/api/v1/workspaces/${workspaceId}/logo`, {
    headers: { Authorization: `Bearer ${session.accessToken}` },
  });
  // 404 when the workspace has no logo: nothing to cache. Otherwise it must be revalidated.
  if (response.status === 200) {
    expect(response.headers.get("cache-control") ?? "").toContain("no-cache");
    expect(response.headers.get("etag")).toBeTruthy();
  }
});

test("the profile picture is revalidated, not served stale from the cache", async () => {
  const session = await loginAs("C.member");
  await uploadAvatar(session, png(32, 32, [16, 185, 129]));
  try {
    const userId = String(session.me.id);
    const first = await fetch(`http://127.0.0.1:8000/api/v1/users/${userId}/avatar`, {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    });
    expect(first.status).toBe(200);
    expect(first.headers.get("cache-control") ?? "").toContain("no-cache");
    const etag = first.headers.get("etag");
    expect(etag).toBeTruthy();
    const again = await fetch(`http://127.0.0.1:8000/api/v1/users/${userId}/avatar`, {
      headers: { Authorization: `Bearer ${session.accessToken}`, "If-None-Match": etag ?? "" },
    });
    expect(again.status).toBe(304);
  } finally {
    await api(session, "DELETE", "/me/avatar");
  }
});
