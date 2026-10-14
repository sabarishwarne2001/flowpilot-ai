/**
 * TRUTHMESH IN DEPTH (campaign session 4, owner task): graph, conflicts, what-if, rebuild and the
 * audit export, on a tenant of its own, plus what another tenant and a viewer can and cannot do.
 *
 * A new Enterprise customer uploads the five sample documents (the master agreement, the order,
 * the goods receipt and two invoices that ask to be paid into different accounts). Then, through
 * the page:
 *   - Rebuild builds the mesh; the cockpit and the graph show every document;
 *   - a document's twin lists its links; "Not linked" sticks across a reload and Undo puts it back;
 *   - a conflict marked "Not a conflict" keeps its decision and its reason through a rebuild;
 *   - the audit export carries every conflict, the decision and its reason, and no cell a
 *     spreadsheet would run as a formula;
 *   - a what-if simulation changes nothing in the mesh and is kept in the history;
 * and through the API:
 *   - a viewer reads and exports but cannot rebuild, decide or simulate;
 *   - another tenant (BizCo, Business plan, TruthMesh included) gets "not found" for every one of
 *     this tenant's ids, and its own export never names this tenant's documents.
 * Its own tenant keeps it independent of 25-truthmesh, which works on Tenant C at the same time.
 */
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "../support/api";
import { TENANTS } from "../support/env";
import { addMember, newCustomer, signInAs, standIn, type Customer } from "../support/journey";
import { samplePath, type SampleName } from "../support/sample-docs";

const SAMPLES: SampleName[] = ["invoice1001", "invoice1002", "purchaseOrder", "goodsReceipt", "contract"];
const TERMINAL = new Set(["COMPLETED", "FAILED", "ERROR", "NEEDS_REVIEW", "REVIEW", "PENDING_REVIEW"]);

interface Overview {
  readonly state: { readonly status: string; readonly last_built_at: string | null };
  readonly documents: number;
  readonly links: number;
  readonly open_conflicts: number;
  readonly money_at_risk_micros?: number | string | null;
}
interface Conflict { readonly id: string; readonly kind: string; readonly status: string; readonly work_item_ids: string[] }
interface GraphNode { readonly work_item_id: string; readonly title: string }
interface Graph { readonly nodes: GraphNode[]; readonly links: Array<{ readonly id: string }> }

let customer: Customer;

async function overview(): Promise<Overview> {
  const result = await api<Overview>(customer.session, "GET", `/workspaces/${customer.wsId}/truthmesh/overview`);
  expect(result.status, result.text.slice(0, 300)).toBe(200);
  return result.body;
}

/** Every conflict, whatever its status (the list filters by one status at a time). */
async function conflicts(): Promise<Conflict[]> {
  const all: Conflict[] = [];
  for (const status of ["OPEN", "ACKNOWLEDGED", "RESOLVED", "DISMISSED"]) {
    const result = await api<{ items: Conflict[] }>(customer.session, "GET",
      `/workspaces/${customer.wsId}/truthmesh/conflicts?status=${status}&limit=200`);
    expect(result.status, result.text.slice(0, 300)).toBe(200);
    all.push(...result.body.items);
  }
  return all;
}

test.describe("TruthMesh in depth — a tenant of its own", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(async () => {
    test.setTimeout(300_000);
    customer = await newCustomer("mesh", "Mesh Depth Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "5");
    for (const sample of SAMPLES) {
      const upload = await uploadFile(customer.session, customer.wsId, samplePath(sample));
      expect(upload.status, upload.text.slice(0, 300)).toBe(201);
    }
    await expect
      .poll(async () => {
        const items = await listWorkItems(customer.session, customer.wsId);
        return items.length === SAMPLES.length && items.every((item) => TERMINAL.has(String(item.status).toUpperCase()));
      }, { timeout: 240_000, intervals: [2_000, 3_000] })
      .toBe(true);
  });

  test("Rebuild on the page builds the mesh; the cockpit and the graph show every document", async ({ page }) => {
    await signInAs(page, customer.email);
    await page.goto(customer.ws("truthmesh"));
    await settle(page);
    await expect(page.getByRole("heading", { name: "TruthMesh", level: 1 })).toBeVisible();

    const started = Date.now();
    await page.getByRole("button", { name: "Rebuild" }).click();
    await expect(page.getByText("Rebuilding the mesh")).toBeVisible();
    await expect(page.getByTestId("mesh-state")).toContainText(/^\s*Built/, { timeout: 120_000 });
    const built = await overview();
    expect(built.state.status).toBe("READY");
    expect(new Date(built.state.last_built_at ?? 0).getTime()).toBeGreaterThanOrEqual(started - 5_000);
    expect(built.documents).toBe(SAMPLES.length);
    expect(built.open_conflicts).toBeGreaterThan(0);

    await expect(page.getByTestId("money-at-risk")).toContainText("$");
    await expect(page.getByTestId("money-at-risk")).not.toContainText("$0.00");
    const graph = page.getByRole("group", { name: /^Document graph: \d+ documents/ });
    await expect(graph).toBeVisible();
    for (const id of ["MSA-E2E-2026", "PO-E2E-5001", "GR-E2E-7001", "INV-E2E-1001", "INV-E2E-1002"]) {
      await expect(graph.getByRole("button", { name: new RegExp(id) }).first()).toBeVisible();
    }
    // The graph's own controls work and leave the page healthy.
    await page.getByRole("button", { name: "Zoom in" }).click();
    await page.getByRole("button", { name: "Fit to view" }).click();
    await expectHealthyPage(page);
  });

  test("a document's twin lists its links; 'Not linked' sticks across a reload and Undo puts it back", async ({ page }) => {
    await signInAs(page, customer.email);
    await page.goto(customer.ws("truthmesh"));
    await settle(page);
    const graph = page.getByRole("group", { name: /^Document graph/ });
    await graph.getByRole("button", { name: /PO-E2E-5001/ }).first().click();
    const twin = page.getByRole("dialog", { name: "Document twin" });
    await expect(twin).toBeVisible();
    await expect(twin).toContainText("PO-E2E-5001");
    const links = twin.getByTestId("twin-link");
    await expect(links.first()).toBeVisible();
    const toInvoice = links.filter({ hasText: "INV-E2E-1001" }).first();
    await expect(toInvoice).toBeVisible();

    const decided = page.waitForResponse((r) => /\/truthmesh\/links\/[0-9a-f-]+\/decision$/.test(r.url()) && r.request().method() === "POST");
    await toInvoice.getByRole("button", { name: "Not linked" }).click();
    expect((await decided).status()).toBe(200);
    await expect(toInvoice).toContainText("rejected by a person");

    // The open twin is part of the address: a reload reopens it, with the decision kept.
    await page.reload();
    await settle(page);
    const reopened = page.getByRole("dialog", { name: "Document twin" });
    await expect(reopened).toContainText("PO-E2E-5001");
    const again = reopened.getByTestId("twin-link").filter({ hasText: "INV-E2E-1001" }).first();
    await expect(again).toContainText("rejected by a person");
    await again.getByRole("button", { name: "Undo" }).click();
    await expect(again).not.toContainText("by a person");
    await expect(again.getByRole("button", { name: "Not linked" })).toBeVisible();
    await page.keyboard.press("Escape");
    await expectHealthyPage(page);
  });

  test("a conflict marked 'Not a conflict' keeps its decision and reason through a rebuild, and the audit export carries it", async ({ page }, testInfo) => {
    await signInAs(page, customer.email);
    await page.goto(customer.ws("truthmesh"));
    await settle(page);
    await page.getByRole("tab", { name: /Conflicts/ }).click();
    const payee = page.locator('[data-testid="mesh-conflict"][data-kind="PAYEE_ACCOUNT_CHANGED"]').first();
    await expect(payee).toBeVisible();
    const id = await payee.getAttribute("data-conflict-id");
    expect(id).toBeTruthy();
    const card = page.locator(`[data-conflict-id="${id}"]`);
    await card.getByRole("button", { expanded: false }).first().click();
    await card.getByRole("button", { name: "Not a conflict" }).click();
    const dialog = page.getByRole("dialog", { name: "Mark as not a conflict" });
    const reason = `Both accounts belong to the supplier (e2e ${Date.now()})`;
    await dialog.getByRole("textbox", { name: "Reason" }).fill(reason);
    await dialog.getByRole("button", { name: "Dismiss" }).click();
    await expect(dialog).toBeHidden();
    await expect(card).toHaveCount(0);

    // A rebuild re-derives the mesh from the documents; a person's decision must survive it.
    const before = Date.now();
    await page.getByRole("button", { name: "Rebuild" }).click();
    await expect(page.getByTestId("mesh-state")).toContainText(/^\s*Built/, { timeout: 120_000 });
    await expect.poll(async () => new Date((await overview()).state.last_built_at ?? 0).getTime(), { timeout: 60_000 })
      .toBeGreaterThanOrEqual(before - 5_000);
    const after = (await conflicts()).find((c) => c.id === id);
    expect(after?.status, "the dismissed conflict kept its id and its decision").toBe("DISMISSED");
    await page.reload();
    await settle(page);
    await page.getByRole("tab", { name: /Conflicts/ }).click();
    await page.getByRole("button", { name: "Dismissed", exact: true }).click();
    const dismissed = page.locator(`[data-conflict-id="${id}"]`);
    await expect(dismissed).toBeVisible();
    await dismissed.getByRole("button", { expanded: false }).first().click();
    await expect(dismissed).toContainText(reason);

    // The audit export: every conflict, the decision and its reason; nothing a spreadsheet runs.
    const downloading = page.waitForEvent("download");
    await page.getByRole("button", { name: "Audit export" }).click();
    const download = await downloading;
    expect(download.suggestedFilename()).toBe("truthmesh-conflicts.csv");
    const file = testInfo.outputPath("truthmesh-conflicts.csv");
    await download.saveAs(file);
    const csv = fs.readFileSync(file, "utf8");
    const lines = csv.trim().split(/\r?\n/);
    expect(lines[0]).toBe("severity,status,kind,title,summary,documents,values,exposure,currency,first_seen,last_seen,resolved_at,resolution_note");
    expect(lines.length - 1).toBe((await conflicts()).length);
    const row = lines.find((line) => line.includes(reason));
    expect(row, "the dismissed conflict's row carries the reason").toBeTruthy();
    expect(row).toContain("DISMISSED");
    expect(row).toContain("Payee account changed");
    expect(row).toContain("INV-E2E-1001");
    expect(row).toContain("INV-E2E-1002");
    for (const line of lines.slice(1)) {
      for (const cell of line.split(",")) expect(cell.replace(/^"/, "")).not.toMatch(/^[=+\-@]/);
    }

    // Put it back for the rest of the run.
    await dismissed.getByRole("button", { name: "Reopen" }).click();
    await expect(dismissed).toHaveCount(0);
    await expectHealthyPage(page);
  });

  test("a what-if simulation changes nothing in the mesh and is kept in the history", async ({ page }) => {
    const before = await overview();
    const conflictsBefore = (await conflicts()).map((c) => `${c.id}:${c.status}`).sort();

    await signInAs(page, customer.email);
    await page.goto(customer.ws("truthmesh"));
    await settle(page);
    await page.getByRole("tab", { name: /What-if/ }).click();
    const origin = page.getByLabel("Starting document");
    const po = origin.locator("option", { hasText: "PO-E2E-5001" }).first();
    await origin.selectOption({ value: (await po.getAttribute("value")) ?? "" });
    await page.getByRole("radio", { name: /Delay/ }).check();
    await page.getByRole("button", { name: "Run simulation" }).click();
    const result = page.getByTestId("ripple-result");
    await expect(result.getByTestId("ripple-node").first()).toBeVisible({ timeout: 30_000 });
    await expect(result.getByRole("button", { name: /INV-E2E-100[12]/ }).first()).toBeVisible();

    const history = page.getByRole("region", { name: "Recent simulations" });
    await expect(history.getByRole("button").first()).toContainText(/PO-E2E-5001|Delay/i);

    // Nothing real moved.
    const after = await overview();
    expect(after.documents).toBe(before.documents);
    expect(after.links).toBe(before.links);
    expect(after.open_conflicts).toBe(before.open_conflicts);
    expect((await conflicts()).map((c) => `${c.id}:${c.status}`).sort()).toEqual(conflictsBefore);

    // Reopened from the history after a reload, the result is the same simulation.
    await page.reload();
    await settle(page);
    await page.getByRole("tab", { name: /What-if/ }).click();
    await page.getByRole("region", { name: "Recent simulations" }).getByRole("button").first().click();
    await expect(page.getByTestId("ripple-result").getByTestId("ripple-node").first()).toBeVisible();
    await expectHealthyPage(page);
  });

  test("a viewer reads and exports the mesh but cannot rebuild, decide or simulate", async () => {
    const viewer = await addMember(customer, "mesh-viewer", "MEMBER", "VIEWER");
    const base = `/workspaces/${customer.wsId}/truthmesh`;
    const someConflict = (await conflicts())[0];
    const graph = await api<Graph>(customer.session, "GET", `${base}/graph`);
    const someLink = graph.body.links[0];
    expect(someConflict && someLink, "the mesh has a conflict and a link to try").toBeTruthy();

    expect((await api(viewer.session, "GET", `${base}/overview`)).status).toBe(200);
    expect((await api(viewer.session, "GET", `${base}/conflicts/export.csv`)).status).toBe(200);
    for (const [method, path, body] of [
      ["POST", `${base}/rebuild`, undefined],
      ["PATCH", `${base}/conflicts/${someConflict.id}`, { status: "DISMISSED", note: "viewer tries" }],
      ["POST", `${base}/links/${someLink.id}/decision`, { decision: "REJECTED" }],
      ["POST", `${base}/simulations`, { work_item_id: graph.body.nodes[0].work_item_id, scenario: "DELAY", days: 10 }],
    ] as const) {
      const refused = await api(viewer.session, method, path, body);
      expect(refused.status, `${method} ${path} as a viewer: ${refused.text.slice(0, 200)}`).toBe(403);
    }
    expect((await conflicts()).find((c) => c.id === someConflict.id)?.status).toBe(someConflict.status);
  });

  test("another tenant gets 'not found' for every id of this mesh, and its export never names these documents", async () => {
    const rival = await loginAs("B.owner");
    const { workspaceId: rivalWs } = await resolveWorkspaceId(rival, TENANTS.B.org, TENANTS.B.ws);
    const graph = await api<Graph>(customer.session, "GET", `/workspaces/${customer.wsId}/truthmesh/graph`);
    const conflict = (await conflicts())[0];
    const node = graph.body.nodes[0];
    const link = graph.body.links[0];
    const simulations = await api<Array<{ id: string }>>(customer.session, "GET", `/workspaces/${customer.wsId}/truthmesh/simulations`);
    const simulation = simulations.body[0];
    expect(conflict && node && link && simulation).toBeTruthy();

    // Straight at this workspace: not a member, so no door at all.
    const direct = await api(rival, "GET", `/workspaces/${customer.wsId}/truthmesh/overview`);
    expect([403, 404], direct.text.slice(0, 200)).toContain(direct.status);

    // Through the rival's own workspace, with this tenant's ids: never found, never changed.
    const own = `/workspaces/${rivalWs}/truthmesh`;
    for (const [method, path, body] of [
      ["GET", `${own}/documents/${node.work_item_id}`, undefined],
      ["PATCH", `${own}/conflicts/${conflict.id}`, { status: "DISMISSED", note: "cross-tenant attempt" }],
      ["POST", `${own}/links/${link.id}/decision`, { decision: "REJECTED" }],
      ["GET", `${own}/simulations/${simulation.id}`, undefined],
    ] as const) {
      const answer = await api(rival, method, path, body);
      expect(answer.status, `${method} ${path}: ${answer.text.slice(0, 200)}`).toBe(404);
    }
    expect((await conflicts()).find((c) => c.id === conflict.id)?.status).toBe(conflict.status);

    const rivalExport = await api<string>(rival, "GET", `${own}/conflicts/export.csv`);
    expect(rivalExport.status).toBe(200);
    expect(rivalExport.text).not.toContain(customer.orgSlug);
    const rivalGraph = await api<Graph>(rival, "GET", `${own}/graph`);
    const theirs = new Set(graph.body.nodes.map((n) => n.work_item_id));
    expect(rivalGraph.body.nodes.filter((n) => theirs.has(n.work_item_id))).toEqual([]);
  });
});
