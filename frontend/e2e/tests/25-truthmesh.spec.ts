/**
 * PHASE 2 — TruthMesh, the cross-document digital twin (Enterprise, capability.truthmesh).
 *
 * Built from the sample documents every run uploads for Tenant C: the master agreement
 * (MSA-E2E-2026), the purchase order (PO-E2E-5001), the goods receipt (GR-E2E-7001) and two invoices
 * (INV-E2E-1001, INV-E2E-1002). INV-E2E-1002 asks to be paid into a different account than
 * INV-E2E-1001, so the mesh must hold a payee-account conflict between them; the invoices together
 * bill more than the order allows, so it must hold an overrun on the order.
 *
 * The plan matrix (02) covers the lock on lower plans: TruthMesh is in routes.ts and its probes.
 */
import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, loginAs, resolveWorkspaceId } from "../support/api";
import { TENANTS, ws } from "../support/env";

interface Overview {
  readonly state: { readonly status: string; readonly last_built_at: string | null };
  readonly documents: number;
  readonly open_conflicts: number;
}

/** Rebuild the mesh from scratch so the run does not depend on when indexing jobs happened to run. */
async function buildMesh(): Promise<void> {
  const session = await loginAs("C.owner");
  const { workspaceId } = await resolveWorkspaceId(session, TENANTS.C.org, TENANTS.C.ws);
  const started = Date.now();
  const rebuild = await api(session, "POST", `/workspaces/${workspaceId}/truthmesh/rebuild`);
  expect(rebuild.status, rebuild.text.slice(0, 300)).toBe(202);
  await expect
    .poll(
      async () => {
        const overview = await api<Overview>(session, "GET", `/workspaces/${workspaceId}/truthmesh/overview`);
        const built = overview.body.state.last_built_at ? new Date(overview.body.state.last_built_at).getTime() : 0;
        return overview.body.state.status === "READY" && built >= started - 5_000 ? overview.body.documents : -1;
      },
      { timeout: 90_000, intervals: [1_000, 2_000] },
    )
    .toBeGreaterThanOrEqual(5);
}

test.describe("TruthMesh", () => {
  test.describe.configure({ mode: "serial" });
  test.beforeAll(async () => {
    test.setTimeout(120_000);
    await buildMesh();
  });

  test.describe("as a contributor", () => {
    test.use({ user: "C.owner" });

    test("the cockpit sums the mesh and a document opens its twin", async ({ page }) => {
      await page.goto(ws("C", "truthmesh"));
      await settle(page);
      await expect(page.getByRole("heading", { name: "TruthMesh", level: 1 })).toBeVisible();
      await expect(page.getByTestId("mesh-state")).toContainText(/Built/);
      // Money is at risk (the overrun and the new payee account), stated in dollars.
      await expect(page.getByTestId("money-at-risk")).toContainText("$");
      await expect(page.getByTestId("money-at-risk")).not.toContainText("$0.00");
      await expect(page.getByTestId("open-conflicts")).not.toContainText(/^\s*Open conflicts\s*0/);
      // The graph draws the order and both invoices, each saying its kind in words.
      const graph = page.getByRole("group", { name: /Document graph/ });
      await expect(graph.getByRole("button", { name: /Purchase order PO-E2E-5001/ }).first()).toBeVisible();
      await expect(graph.getByRole("button", { name: /Invoice INV-E2E-1002/ }).first()).toBeVisible();
      // The order's twin lists what draws on it.
      await graph.getByRole("button", { name: /Purchase order PO-E2E-5001/ }).first().click();
      const twin = page.getByRole("dialog");
      await expect(twin).toBeVisible();
      await expect(twin).toContainText("PO-E2E-5001");
      await expect(twin).toContainText("INV-E2E-1001");
      await page.keyboard.press("Escape");
      await expect(twin).toBeHidden();
      await expectHealthyPage(page);
    });

    test("the new payee account is a conflict with both accounts side by side, and a decision needs a reason", async ({ page }) => {
      await page.goto(ws("C", "truthmesh"));
      await settle(page);
      await page.getByRole("tab", { name: /Conflicts/ }).click();
      const conflict = page.locator('[data-testid="mesh-conflict"][data-kind="PAYEE_ACCOUNT_CHANGED"]').first();
      await expect(conflict).toBeVisible();
      const id = await conflict.getAttribute("data-conflict-id");
      expect(id).toBeTruthy();
      const card = page.locator(`[data-conflict-id="${id}"]`);
      await expect(card).toContainText(/critical/i);
      await card.getByRole("button", { expanded: false }).first().click();
      await expect(card).toContainText("INV-E2E-1001");
      await expect(card).toContainText("INV-E2E-1002");

      // Resolving asks for a reason first; the dialog will not save without one.
      await card.getByRole("button", { name: "Resolve" }).click();
      const dialog = page.getByRole("dialog", { name: "Resolve this conflict" });
      await expect(dialog).toBeVisible();
      const confirm = dialog.getByRole("button", { name: "Resolve" });
      await expect(confirm).toBeDisabled();
      await dialog.getByRole("textbox", { name: "Reason" }).fill("Supplier confirmed the new account by phone (e2e)");
      await confirm.click();
      await expect(dialog).toBeHidden();
      await expect(card).toHaveCount(0);

      // It moves to Resolved, carrying the reason; reopening puts it back among the open ones.
      await page.getByRole("button", { name: "Resolved", exact: true }).click();
      await expect(card).toBeVisible();
      await card.getByRole("button", { expanded: false }).first().click();
      await expect(card).toContainText("Supplier confirmed the new account by phone (e2e)");
      await card.getByRole("button", { name: "Reopen" }).click();
      await expect(card).toHaveCount(0);
      await page.getByRole("button", { name: "Open", exact: true }).click();
      await expect(card).toBeVisible();
      await expectHealthyPage(page);
    });

    test("the discrepancy matrix puts each conflict against the documents it involves", async ({ page }) => {
      await page.goto(ws("C", "truthmesh"));
      await settle(page);
      await page.getByRole("tab", { name: /Conflicts/ }).click();
      await page.getByRole("button", { name: "Discrepancy matrix" }).click();
      const matrix = page.getByTestId("discrepancy-matrix");
      await expect(matrix).toBeVisible();
      await expect(matrix.locator("thead")).toContainText("INV-E2E-1002");
      await expect(matrix.locator("tbody")).toContainText(/Payee account changed/i);
      await expectHealthyPage(page);
    });

    test("a delay on the order ripples to the documents that draw on it", async ({ page }) => {
      await page.goto(ws("C", "truthmesh"));
      await settle(page);
      await page.getByRole("tab", { name: /What-if/ }).click();
      const origin = page.getByLabel("Starting document");
      const po = origin.locator("option", { hasText: "PO-E2E-5001" }).first();
      await origin.selectOption({ value: (await po.getAttribute("value")) ?? "" });
      await page.getByRole("radio", { name: /Delay/ }).check();
      await page.getByRole("button", { name: "Run simulation" }).click();
      const result = page.getByTestId("ripple-result");
      await expect(result.getByTestId("ripple-affected")).not.toContainText(/^\s*Documents affected\s*0/);
      await expect(result.getByRole("button", { name: /INV-E2E-1001/ }).first()).toBeVisible();
      await expectHealthyPage(page);
    });
  });

  test.describe("as a viewer", () => {
    test.use({ user: "C.viewer" });

    test("reads the mesh but cannot rebuild, decide or simulate", async ({ page }) => {
      await page.goto(ws("C", "truthmesh"));
      await settle(page);
      await expect(page.getByTestId("money-at-risk")).toBeVisible();
      await expect(page.getByRole("button", { name: "Rebuild" })).toHaveCount(0);
      await page.getByRole("tab", { name: /Conflicts/ }).click();
      const conflict = page.getByTestId("mesh-conflict").first();
      await conflict.getByRole("button", { expanded: false }).first().click();
      await expect(conflict).toContainText("Contributors decide conflicts.");
      await expect(conflict.getByRole("button", { name: "Resolve" })).toHaveCount(0);
      await page.getByRole("tab", { name: /What-if/ }).click();
      await expect(page.getByRole("button", { name: "Run simulation" })).toBeDisabled();
      await expectHealthyPage(page);
    });
  });
});
