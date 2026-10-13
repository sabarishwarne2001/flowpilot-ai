/**
 * BROWSER-SUITE ISOLATION (campaign session 4, CAMPAIGN-MAP §4.1).
 *
 * The seeded people (support/env.ts USERS) are signed in by many tests at once. "Sign out
 * everywhere" (POST /auth/logout-all) as one of them would end every parallel test's session.
 * The API helper refuses it, and a page signed in through the `user` option has it intercepted
 * before it reaches the server and the test failed with the reason. These two tests prove both.
 */
import { test, expect } from "../support/fixtures";
import { api, loginAs } from "../support/api";
import { ws } from "../support/env";

test.describe("Browser-suite isolation", () => {
  test("the API helper refuses to sign a shared person out everywhere", async () => {
    const session = await loginAs("A.viewer");
    await expect(api(session, "POST", "/auth/logout-all")).rejects.toThrow(/end every session of a seeded person/);
    expect((await api(session, "GET", "/me/profile")).status).toBe(200);  // still signed in
  });

  test.describe("a page signed in as a shared person", () => {
    test.use({ user: "A.viewer" });
    test.fail(true, "the fixture fails a test that signs a shared person out everywhere");

    test("cannot sign it out everywhere", async ({ page }) => {
      await page.goto(ws("A", "work-items"));
      const status = await page.evaluate(async () => (await fetch("/api/v1/auth/logout-all", { method: "POST" })).status);
      expect(status).toBe(409);  // intercepted: the request never reached the server
    });
  });
});
