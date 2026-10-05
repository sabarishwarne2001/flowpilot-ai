/**
 * Proof that each KNOWN_ISSUES entry (support/fixtures.ts) still reproduces.
 * When a bug is fixed, its test here turns red: delete the test AND the
 * KNOWN_ISSUES entry, so the strict fixture fails on that problem again.
 */
import { test, expect, settle } from "../support/fixtures";
import { api } from "../support/api";
import { ws } from "../support/env";

test.describe("known issues still reproduce", () => {
  test.use({ user: "C.admin" });

  test("F-049: a user with no avatar gets a 404 for it on an ordinary page load", async ({ page, problems, session }) => {
    const direct = await api(session, "GET", `/users/${String(session?.me.id)}/avatar`);
    expect(direct.status, "F-049 fixed? remove its KNOWN_ISSUES entry and this test").toBe(404);
    await page.goto(ws("C"));
    await settle(page);
    expect(problems.knownSeen().get("F-049") ?? 0, "the overview page requested the missing avatar").toBeGreaterThan(0);
  });
});
