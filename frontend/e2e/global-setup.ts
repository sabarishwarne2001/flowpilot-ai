/**
 * Runs once before the suite:
 *   1. checks that the API answers (start it with e2e/scripts/start-stack.sh);
 *   2. runs the idempotent seed (backend/scripts/seed_e2e.py);
 *   3. writes the sample documents and uploads them, through the real upload
 *      API, into the workspaces that need them (skipped when already there),
 *      then waits for the worker to finish processing them.
 */
import { execFileSync } from "node:child_process";
import fs from "node:fs";
import path from "node:path";

import { api, listWorkItems, loginAs, resolveWorkspaceId, uploadFile } from "./support/api";
import { API_BASE, BACKEND_DIR, STATE_FILE, TENANTS, USERS, type TenantKey, type UserKey } from "./support/env";
import { SAMPLE, samplePath, writeSampleDocuments, type SampleName } from "./support/sample-docs";

const UPLOADS: Array<{ tenant: TenantKey; as: UserKey; files: SampleName[] }> = [
  {
    tenant: "C",
    as: "C.owner",
    files: [
      "invoice1001",
      "invoice1002",
      "purchaseOrder",
      "goodsReceipt",
      "contract",
      "packet",
      "logoPng",
      "policyPdf",
    ],
  },
  { tenant: "B", as: "B.owner", files: ["invoice1001", "contract", "purchaseOrder", "goodsReceipt"] },
  { tenant: "A", as: "A.owner", files: ["invoice1001"] },
];

const TERMINAL = new Set(["COMPLETED", "FAILED", "ERROR", "NEEDS_REVIEW", "REVIEW", "PENDING_REVIEW"]);

function pythonExecutable(): string {
  if (process.env.E2E_PYTHON) return process.env.E2E_PYTHON;
  const candidates = [
    path.join(BACKEND_DIR, ".venv", "bin", "python"),
    path.join(BACKEND_DIR, ".venv", "Scripts", "python.exe"),
  ];
  return candidates.find((candidate) => fs.existsSync(candidate)) ?? "python3";
}

async function waitForApi(): Promise<void> {
  const deadline = Date.now() + 60_000;
  let last = "";
  while (Date.now() < deadline) {
    try {
      const response = await fetch(`${API_BASE}/health`);
      if (response.ok) return;
      last = `HTTP ${response.status}`;
    } catch (error) {
      last = String(error);
    }
    await new Promise((resolve) => setTimeout(resolve, 1_000));
  }
  throw new Error(
    `The FlowPilot API is not answering at ${API_BASE}/health (${last}). ` +
      "Start the backend first: frontend/e2e/scripts/start-stack.sh (see e2e/README.md).",
  );
}

function runSeed(): Record<string, unknown> {
  const output = execFileSync(pythonExecutable(), ["scripts/seed_e2e.py", "--json"], {
    cwd: BACKEND_DIR,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: 180_000,
  });
  const start = output.indexOf("{");
  const summary = JSON.parse(output.slice(start)) as { users: Record<string, { email: string }> };
  for (const [key, user] of Object.entries(USERS)) {
    if (summary.users[key]?.email !== user.email) {
      throw new Error(`seed_e2e.py and e2e/support/env.ts disagree about ${key}`);
    }
  }
  return summary;
}

async function seedDocuments(): Promise<Record<string, unknown>> {
  writeSampleDocuments();
  const result: Record<string, unknown> = {};
  for (const plan of UPLOADS) {
    const session = await loginAs(plan.as);
    const { workspaceId, organizationId } = await resolveWorkspaceId(
      session,
      TENANTS[plan.tenant].org,
      TENANTS[plan.tenant].ws,
    );
    // With a model (E2E_LLM=1) Tenant C verifies extractions with agents, so a disputed
    // field reaches the review queue the way it does for a customer (see support/llm-mock.mjs).
    if (process.env.E2E_LLM === "1" && plan.tenant === "C") {
      const settings = await api(session, "PUT", `/workspaces/${workspaceId}/document-settings/`, {
        verification_enabled: true,
      });
      if (settings.status >= 300) {
        throw new Error(`enabling verification for tenant C failed: HTTP ${settings.status} ${settings.text.slice(0, 300)}`);
      }
    }
    const existing = await listWorkItems(session, workspaceId);
    const names = new Set(existing.map((item) => String(item.original_filename ?? item.filename ?? item.title ?? "")));
    for (const file of plan.files) {
      const name = SAMPLE[file];
      if ([...names].some((existingName) => existingName.includes(name))) continue;
      const upload = await uploadFile(session, workspaceId, samplePath(file));
      if (upload.status >= 300) {
        throw new Error(`seed upload of ${name} to tenant ${plan.tenant} failed: HTTP ${upload.status} ${upload.text.slice(0, 300)}`);
      }
    }
    // Wait for processing (the worker must be running).
    const deadline = Date.now() + 180_000;
    let items = await listWorkItems(session, workspaceId);
    while (Date.now() < deadline) {
      items = await listWorkItems(session, workspaceId);
      const pending = items.filter((item) => !TERMINAL.has(String(item.status ?? "").toUpperCase()));
      if (pending.length === 0) break;
      await new Promise((resolve) => setTimeout(resolve, 2_000));
    }
    result[plan.tenant] = {
      organizationId,
      workspaceId,
      items: items.map((item) => ({
        id: item.id,
        name: item.original_filename ?? item.filename ?? item.title,
        status: item.status,
      })),
    };
  }
  return result;
}

export default async function globalSetup(): Promise<void> {
  await waitForApi();
  const seed = runSeed();
  const documents = await seedDocuments();
  fs.mkdirSync(path.dirname(STATE_FILE), { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify({ seed, documents }, null, 2));
}
