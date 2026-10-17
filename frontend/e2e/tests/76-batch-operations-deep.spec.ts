/**
 * BATCH OPERATIONS IN DEPTH (campaign session 4, owner task): the dispatch policy moving the lanes,
 * dispatch, the export package and its verification against every way a package can be changed,
 * and what another tenant and a viewer can do. On a tenant of its own, so the policy it changes
 * never races 23-batch-operations (which works on Tenant C).
 *
 *   - the lanes follow the workspace's policy: in a new workspace nothing skips a person (no
 *     calibration is fitted yet), and raising the exception floor turns the disputed invoice into
 *     an exception; Dispatch records the lanes, a document later re-laned shows the lane it was
 *     dispatched in, and a second dispatch replaces the record instead of adding to it;
 *   - a package built by the worker verifies on the page; the same package with one file edited,
 *     one file removed, one file added, or edited with its checksums and manifest regenerated, is
 *     "Tampered", naming the file; a file that is not a package is "Not a package";
 *   - another tenant verifying this tenant's genuine package is told its workspace never issued
 *     it, and gets "not found" for this tenant's batch and package ids;
 *   - a viewer verifies on the page but cannot dispatch, build or download (403 at the server).
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";

import { test, expect, expectHealthyPage, settle } from "../support/fixtures";
import { api, listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "../support/api";
import { API_BASE, TENANTS } from "../support/env";
import { addMember, newCustomer, signInAs, standIn, type Customer } from "../support/journey";
import { samplePath, type SampleName } from "../support/sample-docs";

const SAMPLES: SampleName[] = ["invoice1001", "invoice1002", "purchaseOrder"];
const TERMINAL = new Set(["COMPLETED", "FAILED", "ERROR", "NEEDS_REVIEW", "REVIEW", "PENDING_REVIEW"]);

interface Dispatched { straight_through: number; review: number; exception: number; pending: number; tagged: boolean }
interface Package { id: string; status: string }

let customer: Customer;
let batchId: string;
let packageFile: string;
let packageId: string;

/** Rewrite a zip with Python's zipfile (in every CI image): edit, drop or add one entry. */
function rewriteZip(source: string, target: string, mode: "edit" | "drop" | "add" | "edit-and-reseal"): void {
  const script = `
import hashlib, json, sys, zipfile
src, dst, mode = sys.argv[1:4]
with zipfile.ZipFile(src) as zin:
    names = zin.namelist()
    files = {n: zin.read(n) for n in names}
root = min((n for n in names if n.endswith("manifest.json")), key=len)[: -len("manifest.json")]
data = next(n for n in names if n.endswith("data/extractions.csv"))
if mode in ("edit", "edit-and-reseal"):
    files[data] = files[data] + b"\\nINV-FORGED,999999.00\\n"
if mode == "drop":
    del files[next(n for n in names if n.endswith("data/extractions.json"))]
if mode == "add":
    files[root + "data/extra-invoice.csv"] = b"INV-EXTRA,1.00\\n"
if mode == "edit-and-reseal":
    rel = data[len(root):]
    manifest = json.loads(files[root + "manifest.json"])
    digest = hashlib.sha256(files[data]).hexdigest()
    for entry in manifest.get("files", []):
        if entry.get("path") == rel:
            entry["sha256"] = digest
    files[root + "manifest.json"] = json.dumps(manifest, indent=2).encode()
    resealed = {rel: digest, "manifest.json": hashlib.sha256(files[root + "manifest.json"]).hexdigest()}
    sums = [line for line in files[root + "SHA256SUMS"].decode().splitlines() if line.strip()]
    sums = [f"{resealed[line.split()[-1].lstrip('*')]}  {line.split()[-1].lstrip('*')}"
            if line.split()[-1].lstrip("*") in resealed else line for line in sums]
    files[root + "SHA256SUMS"] = ("\\n".join(sums) + "\\n").encode()
with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
    for name, body in files.items():
        zout.writestr(name, body)
`;
  execFileSync(process.env.E2E_PYTHON ?? "python3", ["-c", script, source, target, mode], { timeout: 60_000 });
}

async function setPolicy(straightThrough: number, exceptionFloor: number): Promise<void> {
  const saved = await api(customer.session, "PUT", `/workspaces/${customer.wsId}/dispatch-policy`, {
    straight_through_min_confidence: straightThrough,
    review_min_confidence: exceptionFloor,
    require_required_fields: false,
    tag_documents: true,
  });
  expect(saved.status, saved.text.slice(0, 300)).toBe(200);
}

test.describe("Batch operations in depth — a tenant of its own", () => {
  test.describe.configure({ mode: "serial" });
  test.setTimeout(240_000);

  test.beforeAll(async () => {
    test.setTimeout(300_000);
    customer = await newCustomer("batches", "Batch Depth Ltd");
    standIn("subscribe", customer.orgSlug, "--plan", "enterprise", "--seats", "5");
    // Scores come from verification; an unverified document has none and always goes to review.
    const verification = await api(customer.session, "PUT", `/workspaces/${customer.wsId}/document-settings/`, {
      verification_enabled: true,
    });
    expect(verification.status, verification.text.slice(0, 300)).toBeLessThan(300);
    const ids: string[] = [];
    for (const sample of SAMPLES) {
      const upload = await uploadFile(customer.session, customer.wsId, samplePath(sample));
      expect(upload.status, upload.text.slice(0, 300)).toBe(201);
      ids.push(String(upload.body.id));
    }
    await expect
      .poll(async () => {
        const items = await listWorkItems(customer.session, customer.wsId);
        return items.length === SAMPLES.length && items.every((item) => TERMINAL.has(String(item.status).toUpperCase()));
      }, { timeout: 240_000, intervals: [2_000, 3_000] })
      .toBe(true);
    const created = await api<{ id: string }>(customer.session, "POST", `/workspaces/${customer.wsId}/processing-batches`, {
      name: "Depth batch",
      work_item_ids: ids,
    });
    expect(created.status, created.text.slice(0, 300)).toBeLessThan(300);
    batchId = created.body.id;
  });

  test("the lanes follow the dispatch policy, and Dispatch records them once per click", async ({ page }) => {
    await signInAs(page, customer.email);
    const lanes = (lane: string) => page.getByTestId(`lane-${lane}`);
    const DISPUTED = "invoice-INV-E2E-1002.pdf";  // its agents disagree on the bank account (96%)

    // The default policy: nothing skips a person in a new workspace (no calibration is fitted
    // yet), so every finished document is in review.
    await setPolicy(0.9, 0.5);
    await page.goto(customer.ws(`batches/${batchId}`));
    await settle(page);
    await expect(lanes("REVIEW")).toContainText(DISPUTED);
    await expect(lanes("REVIEW")).toContainText("No calibration has been fitted");
    await expect(lanes("STRAIGHT_THROUGH")).toContainText("Nothing here.");

    // An exception floor of 100%: the disputed invoice falls below it and becomes an exception.
    await setPolicy(1, 1);
    await page.reload();
    await settle(page);
    await expect(lanes("EXCEPTION")).toContainText(DISPUTED);
    await expect(lanes("REVIEW")).not.toContainText(DISPUTED);

    const dispatched = page.waitForResponse((r) => r.url().endsWith(`/processing-batches/${batchId}/dispatch`));
    await page.getByRole("button", { name: "Dispatch", exact: true }).click();
    const first = (await (await dispatched).json()) as Dispatched;
    expect(first.exception).toBeGreaterThanOrEqual(1);
    expect(first.straight_through + first.review + first.exception + first.pending).toBe(SAMPLES.length);
    await expect(page.getByText(/^Dispatched: 0 straight through, \d+ to review, [1-9]\d* exceptions?/)).toBeVisible();

    // The policy relaxed again: the lane moves back, and the table keeps where it was dispatched.
    await setPolicy(0.9, 0.5);
    await page.reload();
    await settle(page);
    await expect(lanes("REVIEW")).toContainText(DISPUTED);
    const row = page.getByTestId("batch-documents").locator("tr", { hasText: DISPUTED });
    await expect(row).toContainText("dispatched as exception");

    // A second dispatch replaces the record; it does not add to it.
    const second = await api<Dispatched>(customer.session, "POST", `/workspaces/${customer.wsId}/processing-batches/${batchId}/dispatch`);
    expect(second.status, second.text.slice(0, 200)).toBe(200);
    expect(second.body.exception).toBe(0);
    expect(second.body.straight_through + second.body.review + second.body.exception + second.body.pending).toBe(SAMPLES.length);
    await page.reload();
    await settle(page);
    await expect(row).not.toContainText("dispatched as exception");
    await expectHealthyPage(page);
  });

  test("a package built by the worker verifies; every way of changing it is caught", async ({ page }, testInfo) => {
    const built = await api<Package>(customer.session, "POST", `/workspaces/${customer.wsId}/export-packages`, {
      name: "Depth package",
      batch_id: batchId,
    });
    expect(built.status, built.text.slice(0, 300)).toBe(202);
    packageId = built.body.id;
    await expect
      .poll(async () => (await api<Package>(customer.session, "GET", `/workspaces/${customer.wsId}/export-packages/${packageId}`)).body.status,
        { timeout: 120_000, intervals: [1_000, 2_000] })
      .toBe("READY");
    const download = await fetch(`${API_BASE}/workspaces/${customer.wsId}/export-packages/${packageId}/download`, {
      headers: { Authorization: `Bearer ${customer.session.accessToken}` },
    });
    expect(download.status).toBe(200);
    packageFile = testInfo.outputPath("package.zip");
    fs.writeFileSync(packageFile, Buffer.from(await download.arrayBuffer()));

    await signInAs(page, customer.email);
    await page.goto(customer.ws("batches"));
    await settle(page);
    await page.getByRole("button", { name: "Verify a package" }).click();
    const result = page.getByTestId("verify-result");
    const input = page.getByLabel("Package file");

    await input.setInputFiles(packageFile);
    await expect(result).toHaveAttribute("data-verdict", "VERIFIED", { timeout: 30_000 });

    const cases = [
      { mode: "edit", says: /extractions\.csv/ },
      { mode: "drop", says: /extractions\.json/ },
      { mode: "add", says: /extra-invoice\.csv/ },
      { mode: "edit-and-reseal", says: /not the one Servabo issued/ },
    ] as const;
    for (const { mode, says } of cases) {
      const changed = testInfo.outputPath(`package-${mode}.zip`);
      rewriteZip(packageFile, changed, mode);
      await input.setInputFiles(changed);
      await expect(result, mode).toHaveAttribute("data-verdict", "TAMPERED", { timeout: 30_000 });
      await expect(result, mode).toContainText(says);
    }

    const notAPackage = testInfo.outputPath("notes.zip");
    fs.writeFileSync(notAPackage, "this is not a zip archive");
    await input.setInputFiles(notAPackage);
    await expect(result).toHaveAttribute("data-verdict", "INVALID");
    await expectHealthyPage(page);
  });

  test("another tenant is told its workspace never issued this package, and finds none of these ids", async () => {
    const rival = await loginAs("C.owner");
    const { workspaceId: rivalWs } = await resolveWorkspaceId(rival, TENANTS.C.org, TENANTS.C.ws);
    const form = new FormData();
    form.append("file", new Blob([fs.readFileSync(packageFile)], { type: "application/zip" }), "package.zip");
    const verdict = await api<{ verdict: string }>(rival, "POST", `/workspaces/${rivalWs}/export-packages/verify`, form);
    expect(verdict.status, verdict.text.slice(0, 200)).toBe(200);
    expect(verdict.body.verdict).toBe("UNRECOGNISED");

    for (const [method, path] of [
      ["GET", `/workspaces/${rivalWs}/processing-batches/${batchId}`],
      ["POST", `/workspaces/${rivalWs}/processing-batches/${batchId}/dispatch`],
      ["GET", `/workspaces/${rivalWs}/export-packages/${packageId}`],
      ["GET", `/workspaces/${rivalWs}/export-packages/${packageId}/download`],
    ] as const) {
      const answer = await api(rival, method, path);
      expect(answer.status, `${method} ${path}: ${answer.text.slice(0, 200)}`).toBe(404);
    }
    const direct = await api(rival, "GET", `/workspaces/${customer.wsId}/processing-batches/${batchId}`);
    expect([403, 404]).toContain(direct.status);
    const theirs = await api<{ items: Array<{ id: string }> }>(rival, "GET", `/workspaces/${rivalWs}/processing-batches`);
    expect(theirs.body.items.map((b) => b.id)).not.toContain(batchId);
  });

  test("a viewer verifies on the page but cannot dispatch, build or download", async ({ page }) => {
    const viewer = await addMember(customer, "batch-viewer", "MEMBER", "VIEWER");
    await signInAs(page, viewer.email);
    await page.goto(customer.ws(`batches/${batchId}`));
    await settle(page);
    await expect(page.getByRole("button", { name: "Dispatch", exact: true })).toHaveCount(0);
    await page.goto(customer.ws("batches"));
    await page.getByRole("button", { name: "Verify a package" }).click();
    await page.getByLabel("Package file").setInputFiles(packageFile);
    await expect(page.getByTestId("verify-result")).toHaveAttribute("data-verdict", "VERIFIED", { timeout: 30_000 });

    for (const [method, path, body] of [
      ["POST", `/workspaces/${customer.wsId}/processing-batches/${batchId}/dispatch`, undefined],
      ["POST", `/workspaces/${customer.wsId}/export-packages`, { name: "viewer", batch_id: batchId }],
      ["GET", `/workspaces/${customer.wsId}/export-packages/${packageId}/download`, undefined],
      ["PUT", `/workspaces/${customer.wsId}/dispatch-policy`, { straight_through_min_confidence: 0, review_min_confidence: 0 }],
    ] as const) {
      const refused = await api(viewer.session, method, path, body);
      expect(refused.status, `${method} ${path} as a viewer: ${refused.text.slice(0, 200)}`).toBe(403);
    }
    await expectHealthyPage(page);
  });
});
