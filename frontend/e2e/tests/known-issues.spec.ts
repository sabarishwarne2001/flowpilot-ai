/**
 * Regression proofs for findings that used to be KNOWN_ISSUES entries
 * (support/fixtures.ts). While a finding is open, its test here proves it still
 * reproduces; once fixed, the entry is removed and the test proves the fix.
 */
import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { org, ws } from "../support/env";

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

test.describe("the console is not rebuilt when your display preferences arrive", () => {
  test.use({ user: "C.owner" });

  test("F-121: what you type before the profile has loaded is kept", async ({ page }) => {
    // The profile (time zone, language) answers 2 s late, as on a slow connection.
    const PROFILE = /\/api\/v1\/me\/profile$/;
    let profileArrived = false;
    page.on("response", (response) => {
      if (PROFILE.test(response.url())) profileArrived = true;
    });
    await page.route(PROFILE, async (route) => {
      await new Promise((resolve) => setTimeout(resolve, 2_000));
      await route.continue();
    });

    await page.goto(org("C", "settings"));
    const name = page.getByRole("textbox", { name: "Organization name" });
    await expect(name).toBeEditable({ timeout: 15_000 });
    await name.fill("Typed before the profile arrived");
    await expect.poll(() => profileArrived, { timeout: 15_000 }).toBe(true);
    await settle(page);
    await expect(name).toHaveValue("Typed before the profile arrived");
  });
});
