/**
 * SEATS (campaign session 1): the organization is the customer and every member
 * occupies a seat. Free includes the seats its plan declares (two); a paid plan
 * holds the seats its subscription bought, and owners, admins and billing managers
 * buy more (the price shown and confirmed first). The invite panel says how many
 * seats are in use and, when none is left, why and where to go next.
 *
 * Sandbox: no Stripe key, so a purchase is refused politely (503, "Billing is not
 * configured"); the tests assert the price is shown before that point.
 */
import { test, expect, expectHealthyPage } from "../support/fixtures";
import { api, loginAs } from "../support/api";
import { TENANTS, org, runId } from "../support/env";

interface MeContext {
  organizations: Array<{ organization_id: string; organization_slug: string }>;
}

async function organizationId(userKey: "A.billing" | "P.superadmin" | "C.owner", slug: string): Promise<string> {
  const session = await loginAs(userKey);
  const context = await api<MeContext>(session, "GET", "/me/context");
  const found = context.body.organizations.find((o) => o.organization_slug === slug);
  if (!found) throw new Error(`${slug} not visible to ${userKey}`);
  return found.organization_id;
}

test.describe("Seats on a paid plan", () => {
  test.use({ user: "A.billing" });

  test("a billing manager sees the seat price before buying, and the purchase is refused politely without a gateway", async ({ page, problems }) => {
    problems.allowHttp(/\/billing\/seats$/, [503], "no Stripe key in the sandbox: the purchase cannot reach the gateway");
    await page.goto(org("A", "billing"));
    const seats = page.getByRole("region", { name: "Seats" });
    await expect(seats).toContainText("In use");
    await expect(seats).toContainText("Paid for");

    await seats.getByRole("button", { name: "One seat more" }).click();
    await seats.getByRole("button", { name: "Review change" }).click();
    const dialog = seats.getByRole("dialog", { name: "Confirm seat change" });
    await expect(dialog).toContainText("$49.00 per seat per month", { timeout: 15_000 });
    await dialog.getByRole("button", { name: "Buy 1 seat" }).click();
    await expect(dialog.getByRole("alert")).toContainText("Billing is not configured", { timeout: 15_000 });
    await dialog.getByRole("button", { name: "Cancel" }).click();
    await expectHealthyPage(page);
  });
});

test.describe("Seats on Free", () => {
  test.use({ user: "P.superadmin" });

  test("Free includes two seats: the invite panel counts them and stops at the second", async ({ page }) => {
    const organizationIdP = await organizationId("P.superadmin", TENANTS.P.org);
    const admin = await loginAs("P.superadmin");
    // Converge: a previous run may have left a pending invitation behind.
    const pending = await api<Array<{ id: string; status: string }>>(
      admin, "GET", `/organizations/${organizationIdP}/invitations?status=PENDING`,
    );
    for (const row of Array.isArray(pending.body) ? pending.body : []) {
      await api(admin, "POST", `/organizations/${organizationIdP}/invitations/${row.id}/revoke`);
    }

    await page.goto(org("P", "members"));
    const panel = page.getByRole("region", { name: "Invite people" });
    await expect(panel.getByTestId("invite-seat-line")).toContainText("1 of 2 seats in use");

    const invitee = `free-seat-${runId()}@e2e.example.com`;
    await panel.getByRole("textbox", { name: "Email address" }).fill(invitee);
    await panel.getByRole("button", { name: "Send invitation" }).click();
    await expect(panel.getByTestId("invite-seat-line")).toContainText("2 of 2 seats in use", { timeout: 15_000 });
    await expect(panel.getByRole("status")).toContainText("Every seat the plan includes is taken");
    await expect(panel.getByRole("link", { name: "See plans in Billing" })).toBeVisible();
    await panel.getByRole("textbox", { name: "Email address" }).fill(`another-${runId()}@e2e.example.com`);
    await expect(panel.getByRole("button", { name: "Send invitation" })).toBeDisabled();

    // The server refuses too, whatever the page shows.
    const refused = await api<{ code: string; details: { reason: string; seat_capacity: number } }>(
      admin, "POST", `/organizations/${organizationIdP}/invitations`,
      { email: `third-${runId()}@e2e.example.com`, organization_role: "MEMBER" },
    );
    expect(refused.status).toBe(409);
    expect(refused.body.code).toBe("SEAT_LIMIT_EXCEEDED");
    expect(refused.body.details.reason).toBe("SEAT_LIMIT_REACHED");
    expect(refused.body.details.seat_capacity).toBe(2);

    await panel.getByRole("button", { name: `Revoke the invitation to ${invitee}` }).click();
    await expect(panel.getByTestId("invite-seat-line")).toContainText("1 of 2 seats in use", { timeout: 15_000 });
    await expectHealthyPage(page);
  });
});

test.describe("Seats on the Enterprise organization", () => {
  test.use({ user: "C.owner" });

  test("the invite panel and the seats panel agree with the server", async ({ page }) => {
    const organizationIdC = await organizationId("C.owner", TENANTS.C.org);
    const owner = await loginAs("C.owner");
    const state = await api<{ seat_capacity: number; seats_used: number }>(
      owner, "GET", `/organizations/${organizationIdC}/billing/subscription`,
    );
    expect(state.status).toBe(200);

    await page.goto(org("C", "members"));
    await expect(page.getByTestId("invite-seat-line")).toContainText(
      `${state.body.seats_used} of ${state.body.seat_capacity} seats in use`,
    );
    await page.goto(org("C", "billing"));
    const seats = page.getByRole("region", { name: "Seats" });
    await expect(seats).toContainText(String(state.body.seat_capacity));
    await expectHealthyPage(page);
  });
});
