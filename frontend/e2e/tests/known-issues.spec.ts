/**
 * Regression proofs for findings that used to be KNOWN_ISSUES entries
 * (support/fixtures.ts). While a finding is open, its test here proves it still
 * reproduces; once fixed, the entry is removed and the test proves the fix.
 */
import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { ws } from "../support/env";

const AVATAR_URL = /\/api\/v1\/users\/[0-9a-f-]+\/avatar(\?|$)/;

test.describe("fixed known issues stay fixed", () => {
  test.use({ user: "C.admin" });

  test("F-049: a user with no avatar is not asked for one on an ordinary page load", async ({ page, session }) => {
    const me = await api(session, "GET", "/auth/me");
    expect((me.body as { has_avatar?: boolean }).has_avatar, "C.admin has no avatar in the seed").toBe(false);
    const avatarRequests: string[] = [];
    page.on("request", (request) => {
      if (AVATAR_URL.test(request.url())) avatarRequests.push(request.url());
    });
    await page.goto(ws("C"));
    await settle(page);
    expect(avatarRequests, "the sidebar requested an avatar the server said does not exist").toEqual([]);
  });
});
