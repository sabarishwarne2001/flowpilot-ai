/**
 * F-139 — the invite form offers only the organization roles the inviter may grant.
 *
 * The server lets an OWNER invite MEMBER, BILLING or ADMIN and an ADMIN only MEMBER or BILLING
 * (promotion to ADMIN is the owner's, so an admin cannot manufacture a peer). The form offered
 * "Admin" to every inviter, so an admin who picked it filled the form in and was refused.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { org } from "../support/env";

const roleOptions = async (page: import("@playwright/test").Page): Promise<string[]> => {
  const select = page.getByRole("combobox", { name: "Organization role" });
  await expect(select).toBeVisible();
  return select.locator("option").allInnerTexts();
};

test.describe("as an organization admin", () => {
  test.use({ user: "A.admin" });

  test("the invite form does not offer Admin", async ({ page }) => {
    await page.goto(org("A", "members"));
    const options = await roleOptions(page);
    expect(options).toEqual(expect.arrayContaining(["Member", "Billing"]));
    expect(options).not.toContain("Admin");
    expect(options).not.toContain("Owner");
    await expectHealthyPage(page);
  });
});

test.describe("as the owner", () => {
  test.use({ user: "A.owner" });

  test("the invite form offers Admin, never Owner", async ({ page }) => {
    await page.goto(org("A", "members"));
    const options = await roleOptions(page);
    expect(options).toEqual(expect.arrayContaining(["Member", "Billing", "Admin"]));
    expect(options).not.toContain("Owner");
    await expectHealthyPage(page);
  });
});
