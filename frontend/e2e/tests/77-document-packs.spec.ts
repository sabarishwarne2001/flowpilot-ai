/**
 * DOCUMENT PACKS (Settings → Documents; campaign session 4, owner task).
 *
 * A pack gives a document type its field list, its suggested checks and a redaction profile.
 * Every pack in the catalogue (HR, Healthcare, Legal, Logistics, KYC) is reviewed on the page: each
 * field, each suggested check, and the redaction profile by its name and what it removes (never the
 * internal key); the Healthcare packs carry the Safe Harbor notice and never claim "HIPAA compliant".
 * Then, in a new customer's workspace, a pack is applied (not yet enabled), enabled and disabled,
 * each surviving a reload; applying twice makes one copy; the organization's second workspace and
 * another tenant are untouched. A contributor reviews but cannot apply or enable; the server refuses
 * a contributor and a viewer (who is not shown the Documents section at all).
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId } from "../support/api";
import { TENANTS, runId, ws } from "../support/env";
import { newCustomer, signInAs, standIn, type Customer } from "../support/journey";

interface Pack {
  readonly id: string;
  readonly industry: string;
  readonly document_type: string;
  readonly label: string;
  readonly schema_fields: { readonly properties?: Record<string, { readonly type?: string }> };
  readonly assertions: ReadonlyArray<{ readonly sentence: string; readonly severity: string }>;
  readonly redaction_profile: string | null;
  readonly applied: boolean;
  readonly enabled: boolean;
  readonly safe_harbor_notice: string | null;
}

/** What a person reads for each redaction profile: its name and what it removes. */
const PROFILE_NAMES: Record<string, { name: string; removes: RegExp }> = {
  hipaa_safe_harbor: { name: "HIPAA Safe Harbor", removes: /dates of birth/i },
  india_kyc: { name: "India KYC", removes: /Aadhaar/ },
  financial: { name: "Financial", removes: /IBAN/ },
  all_identifiers: { name: "All identifiers", removes: /every kind of identifier/i },
};
const INDUSTRIES = ["HR", "HEALTHCARE", "LEGAL", "LOGISTICS", "KYC"];

async function packsOf(session: Awaited<ReturnType<typeof loginAs>>, workspaceId: string): Promise<Pack[]> {
  const listed = await api<Pack[]>(session, "GET", `/workspaces/${workspaceId}/document-presets`);
  expect(listed.status, listed.text.slice(0, 300)).toBe(200);
  return listed.body;
}

const packCard = (page: import("@playwright/test").Page, label: string) =>
  page.getByRole("listitem").filter({ has: page.getByRole("heading", { name: label, exact: true, level: 4 }) });

test.describe("Document packs — every pack reviewed", () => {
  test.use({ user: "C.owner" });

  test("each pack shows its fields, its suggested checks and its redaction profile by name", async ({ page }) => {
    const session = await loginAs("C.owner");
    const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
    const packs = await packsOf(session, workspaceId);
    expect(new Set(packs.map((p) => p.industry))).toEqual(new Set(INDUSTRIES));
    expect(packs.length).toBeGreaterThanOrEqual(10);

    await page.goto(ws("C", "settings?section=document"));
    await settle(page);
    await expect(page.getByRole("heading", { name: "Document packs" })).toBeVisible();

    for (const pack of packs) {
      const card = packCard(page, pack.label);
      await expect(card, pack.label).toHaveCount(1);
      const fields = Object.keys(pack.schema_fields.properties ?? {});
      expect(fields.length, `${pack.label} has fields`).toBeGreaterThan(0);
      expect(pack.assertions.length, `${pack.label} has suggested checks`).toBeGreaterThan(0);
      expect(pack.redaction_profile, `${pack.label} has a redaction profile`).toBeTruthy();

      const review = card.getByRole("button", { name: `Review ${fields.length} fields` });
      await expect(review).toHaveAttribute("aria-expanded", "false");
      await review.click();
      await expect(card.getByRole("button", { name: "Hide details" })).toHaveAttribute("aria-expanded", "true");

      const fieldList = card.getByRole("list", { name: "Extracted fields" });
      for (const field of fields) await expect(fieldList, `${pack.label}: ${field}`).toContainText(field);
      const checks = card.getByRole("list", { name: "Suggested checks" });
      for (const assertion of pack.assertions) await expect(checks).toContainText(assertion.sentence);

      const profile = PROFILE_NAMES[pack.redaction_profile ?? ""];
      expect(profile, `${pack.label}: a profile the page can name (${pack.redaction_profile})`).toBeTruthy();
      const redaction = card.getByTestId("pack-redaction-profile");
      await expect(redaction).toContainText(profile.name);
      await expect(redaction).toContainText(profile.removes);
      await expect(redaction).not.toContainText(pack.redaction_profile ?? "");

      // What it does and does not do, said plainly: matched by its phrases; checks and profile are
      // recommendations nothing applies by itself.
      await expect(card.getByTestId("pack-hints")).toContainText("at least");
      await expect(card).toContainText("enabling the pack does not create them");
      await expect(redaction).toContainText("enabling the pack does not redact anything");

      if (pack.redaction_profile === "hipaa_safe_harbor") {
        expect(pack.safe_harbor_notice).toBeTruthy();
        await expect(redaction).toContainText(pack.safe_harbor_notice ?? "");
      } else {
        expect(pack.safe_harbor_notice).toBeNull();
      }
      await expect(card).not.toContainText(/HIPAA[- ]compliant/i);
      await card.getByRole("button", { name: "Hide details" }).click();
    }
    await expectHealthyPage(page);
  });
});

test.describe("Document packs — apply, review, enable", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(180_000);
  let customer: Customer;
  let secondWorkspaceId: string;
  const LABEL = "Master services agreement";

  test.beforeAll(async () => {
    customer = await newCustomer("packs", "Packs Review Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "business", "--seats", "2");  // a second workspace
    const second = await api<{ id: string }>(customer.session, "POST", `/organizations/${customer.orgId}/workspaces`, {
      workspace_name: `Second ${runId()}`,
    });
    expect(second.status, second.text).toBeLessThan(300);
    secondWorkspaceId = second.body.id;
  });

  test("an admin applies a pack, reviews it, enables it and disables it; each step survives a reload", async ({ page }) => {
    await signInAs(page, customer.email);
    await page.goto(customer.ws("settings?section=document"));
    await settle(page);
    const card = packCard(page, LABEL);
    await expect(card.getByRole("button", { name: "Enable" })).toHaveCount(0);

    await card.getByRole("button", { name: "Apply to workspace" }).click();
    await expect(page.getByText(`${LABEL} applied. Review it below, then enable it.`)).toBeVisible();
    await expect(card).toContainText("Applied but not enabled.");
    // Applying opens the review: the fields and checks are on screen before enabling.
    await expect(card.getByRole("list", { name: "Extracted fields" })).toBeVisible();
    await expect(card.getByRole("list", { name: "Suggested checks" })).toBeVisible();
    await expect(card.getByRole("button", { name: "Apply to workspace" })).toHaveCount(0);

    await page.reload();
    await settle(page);
    await expect(card).toContainText("Applied but not enabled.");
    let mine = (await packsOf(customer.session, customer.wsId)).find((p) => p.label === LABEL);
    expect([mine?.applied, mine?.enabled]).toEqual([true, false]);

    await card.getByRole("button", { name: "Enable" }).click();
    await expect(page.getByText(`${LABEL} is enabled: new documents that match it are read with its fields.`)).toBeVisible();
    await expect(card).toContainText("Enabled");
    await expect(card).not.toContainText("Applied but not enabled.");
    await page.reload();
    await settle(page);
    await expect(card.getByRole("button", { name: "Disable" })).toBeVisible();
    mine = (await packsOf(customer.session, customer.wsId)).find((p) => p.label === LABEL);
    expect(mine?.enabled).toBe(true);

    // Applying again (a second tab, a double click) keeps one copy and does not switch it off.
    const again = await api<Pack>(customer.session, "POST", `/workspaces/${customer.wsId}/document-presets/apply`, { preset_id: mine?.id });
    expect([200, 409], again.text.slice(0, 200)).toContain(again.status);
    const listed = (await packsOf(customer.session, customer.wsId)).filter((p) => p.label === LABEL);
    expect(listed.map((p) => [p.applied, p.enabled])).toEqual([[true, true]]);

    await card.getByRole("button", { name: "Disable" }).click();
    await expect(page.getByText(`${LABEL} is disabled.`)).toBeVisible();
    await page.reload();
    await settle(page);
    await expect(card.getByRole("button", { name: "Enable" })).toBeVisible();
    await expect(card).toContainText("Applied but not enabled.");
    await expectHealthyPage(page);
  });

  test("the pack stays in its workspace: the second workspace and another tenant are untouched", async () => {
    // Enabled again for the check, so "untouched" is measured against a live pack.
    const pack = (await packsOf(customer.session, customer.wsId)).find((p) => p.label === LABEL);
    const enabled = await api(customer.session, "POST", `/workspaces/${customer.wsId}/document-presets/${pack?.id}/enabled`, { enabled: true });
    expect(enabled.status, enabled.text.slice(0, 200)).toBe(200);

    const second = (await packsOf(customer.session, secondWorkspaceId)).find((p) => p.label === LABEL);
    expect([second?.applied, second?.enabled]).toEqual([false, false]);

    const rival = await loginAs("B.owner");
    const { workspaceId: rivalWs } = await resolveWorkspaceId(rival, TENANTS.B.org, TENANTS.B.ws);
    const theirs = (await packsOf(rival, rivalWs)).find((p) => p.label === LABEL);
    expect(theirs?.enabled).toBe(false);
    // The rival cannot reach this workspace's packs, nor switch this one off from its own workspace.
    const direct = await api(rival, "GET", `/workspaces/${customer.wsId}/document-presets`);
    expect([403, 404]).toContain(direct.status);
    const fromOwn = await api(rival, "POST", `/workspaces/${rivalWs}/document-presets/${pack?.id}/enabled`, { enabled: false });
    expect(fromOwn.status, fromOwn.text.slice(0, 200)).not.toBe(500);
    expect((await packsOf(customer.session, customer.wsId)).find((p) => p.label === LABEL)?.enabled).toBe(true);
  });
});

test.describe("Document packs — contributor and viewer", () => {
  test.use({ user: "C.member" });

  test("a contributor reviews the packs but cannot apply or enable them; a viewer cannot either", async ({ page }) => {
    await page.goto(ws("C", "settings?section=document"));
    await settle(page);
    await expect(page.getByRole("heading", { name: "Document packs" })).toBeVisible();
    await expect(page.getByRole("button", { name: "Apply to workspace" })).toHaveCount(0);
    await expect(page.getByRole("button", { name: /^(Enable|Disable)$/ })).toHaveCount(0);
    await page.getByRole("button", { name: /^Review \d+ fields$/ }).first().click();
    await expect(page.getByRole("list", { name: "Extracted fields" }).first()).toBeVisible();
    await expect(page.getByTestId("pack-redaction-profile").first()).toBeVisible();

    // The server refuses both roles, whatever a page shows (a viewer does not see this section).
    for (const who of ["C.member", "C.viewer"] as const) {
      const session = await loginAs(who);
      const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
      const pack = (await packsOf(session, workspaceId))[0];
      const apply = await api(session, "POST", `/workspaces/${workspaceId}/document-presets/apply`, { preset_id: pack.id });
      expect(apply.status, `${who} apply: ${apply.text.slice(0, 200)}`).toBe(403);
      const enable = await api(session, "POST", `/workspaces/${workspaceId}/document-presets/${pack.id}/enabled`, { enabled: true });
      expect(enable.status, `${who} enable: ${enable.text.slice(0, 200)}`).toBe(403);
    }
    await expectHealthyPage(page);
  });
});
