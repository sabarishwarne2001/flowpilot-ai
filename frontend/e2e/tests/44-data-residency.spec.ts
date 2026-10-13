/**
 * Campaign session 3 (settings truthfulness, F-301, N-066) — the data residency card says what
 * the region pins.
 *
 * It said "Where this tenant's documents, avatars and export archives are physically stored" and,
 * for EU, "Required by most GDPR data processing agreements". The region decides where export
 * archives and branding files are written; documents, profile pictures and everything in the
 * database stay where the deployment runs. The card now lists both, from the server's statement
 * (which `tests/api/test_residency_says_what_it_pins.py` keeps equal to the code).
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org } from "../support/env";

test.describe("Data residency", () => {
  test.use({ user: "C.owner" });

  test("the card lists what the region pins and what stays where the deployment runs", async ({ page }) => {
    await page.goto(org("C", "compliance"));
    const card = page.getByTestId("residency-scope");
    await expect(card).toBeVisible();
    await expect(card.getByTestId("residency-pinned")).toContainText("Compliance export archives");
    await expect(card.getByTestId("residency-pinned")).toContainText("Branding files");
    await expect(card.getByTestId("residency-not-pinned")).toContainText("Documents");
    await expect(card.getByTestId("residency-not-pinned")).toContainText("search index");
    await expect(page.getByText(/documents, avatars and export archives are\s+physically stored/i)).toHaveCount(0);
    await expect(page.getByText(/Required by most GDPR/i)).toHaveCount(0);
    await expectHealthyPage(page);
  });
});
