/**
 * Form inventory: opens the main "create" flows and writes the accessibility
 * tree (roles and names, which is what the tests select by) into
 * e2e/.explore/forms/. A tool for writing tests, not part of the suite.
 *   E2E_TEST_DIR=./tools npx playwright test -c e2e forms
 */
import fs from "node:fs";
import path from "node:path";

import { test, settle } from "../support/fixtures";
import { E2E_DIR, org, ws } from "../support/env";
import { listWorkItems, loginAs, resolveWorkspaceId } from "../support/api";

const OUT = path.join(E2E_DIR, ".explore", "forms");

const ONLY = process.env.E2E_FORMS_ONLY ? new RegExp(process.env.E2E_FORMS_ONLY) : null;
const FLOWS: Array<{ name: string; url: string; click?: string[] }> = [
  { name: "org-api-keys-new", url: org("C", "api-keys"), click: ["New key"] },
  { name: "org-webhooks-new", url: org("C", "webhooks"), click: ["New endpoint"] },
  { name: "org-members", url: org("C", "members") },
  { name: "org-billing", url: org("C", "billing") },
  { name: "org-billing-free", url: org("P", "billing") },
  { name: "org-compliance-erase", url: org("C", "compliance"), click: ["Erase a subject"] },
  { name: "org-developer-issue", url: org("C", "developer"), click: ["Issue key"] },
  { name: "org-analytics-add", url: org("C", "analytics"), click: ["Add destination"] },
  { name: "org-identity-sso", url: org("C", "identity"), click: ["Single sign-on"] },
  { name: "org-identity-scim", url: org("C", "identity"), click: ["SCIM"] },
  { name: "org-egress", url: org("C", "egress") },
  { name: "org-byok", url: org("C", "byok") },
  { name: "org-email", url: org("C", "email-settings") },
  { name: "org-audit", url: org("C", "audit"), click: ["Inspect"] },
  { name: "org-general", url: org("C", "settings") },
  { name: "ws-automation-new", url: ws("C", "automation"), click: ["Create New Rule"] },
  { name: "ws-erp-new-target", url: ws("C", "erp"), click: ["New target"] },
  { name: "ws-corroboration-new", url: ws("C", "corroboration"), click: ["New comparison"] },
  { name: "ws-obligations-new", url: ws("C", "obligations"), click: ["New obligation"] },
  { name: "ws-obligations", url: ws("C", "obligations") },
  { name: "ws-packet-splits", url: ws("C", "packet-splits") },
  { name: "ws-cases", url: ws("C", "cases") },
  { name: "ws-verification", url: ws("C", "verification") },
  { name: "ws-assertions", url: ws("C", "assertions") },
  { name: "ws-assistant-new", url: ws("C", "assistant"), click: ["New"] },
  { name: "ws-settings-general", url: ws("C", "settings"), click: ["General Name, locale and members"] },
  { name: "ws-settings-ai", url: ws("C", "settings"), click: ["AI What runs, and the defaults"] },
  { name: "ws-settings-documents", url: ws("C", "settings"), click: ["Documents Extraction and schema presets"] },
  { name: "ws-settings-sessions", url: ws("C", "settings"), click: ["Active sessions Where you are signed in"] },
  { name: "ws-procurement-policies", url: ws("C", "procurement/policies") },
  { name: "ws-work-items", url: ws("C", "work-items") },
  { name: "ws-process-discovery", url: ws("C", "process"), click: ["Discovery"] },
  { name: "ws-extraction-memory", url: ws("C", "extraction-memory") },
];

test.use({ user: "C.owner" });
test.setTimeout(900_000);

test("dump the accessibility tree of each form", async ({ page, problems }) => {
  fs.mkdirSync(OUT, { recursive: true });
  const session = await loginAs("C.owner");
  const { workspaceId } = await resolveWorkspaceId(session, "caretakers-global", "operations");
  const items = await listWorkItems(session, workspaceId);
  const invoice = items.find((item) => String(item.original_filename ?? "").includes("INV-E2E-1001"));
  FLOWS.push({ name: "ws-work-item-detail", url: ws("C", `work-items/${invoice?.id}`) });
  for (const flow of FLOWS.filter((f) => !ONLY || ONLY.test(f.name))) {
    try {
      await page.goto(flow.url);
      await settle(page, 800);
      for (const name of flow.click ?? []) {
        await page.getByRole("button", { name, exact: false }).first().click({ timeout: 5_000 });
        await settle(page, 600);
      }
      const dialog = page.locator("[role=dialog]:visible,[role=alertdialog]:visible");
      const scope = (await dialog.count()) > 0 ? dialog.first() : page.locator("body");
      // Drop the sidebar: everything indented under "- complementary".
      const lines = (await scope.ariaSnapshot()).split("\n");
      const kept: string[] = [];
      let skipping = false;
      for (const line of lines) {
        if (/^- complementary/.test(line)) { skipping = true; continue; }
        if (skipping && /^- /.test(line)) skipping = false;
        if (!skipping) kept.push(line);
      }
      const tree = kept.join("\n");
      fs.writeFileSync(path.join(OUT, `${flow.name}.yaml`), `# ${page.url()}\n${tree}`);
      await page.screenshot({ path: path.join(OUT, `${flow.name}.png`) });
    } catch (error) {
      fs.writeFileSync(path.join(OUT, `${flow.name}.error.txt`), String(error));
    }
  }
  problems.allowHttp(/.*/, "any", "inventory tool");
  problems.allowConsole(/.*/, "inventory tool");
  problems.all.splice(0, problems.all.length);
});
