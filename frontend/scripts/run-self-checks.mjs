#!/usr/bin/env node
/**
 * Runs the app's built-in self-checks (tenant resolution, tenant paths, tenant reconciliation,
 * permission parity) outside the browser and exits 1 on any failure.
 *
 *   npm run check:self
 *
 * They used to run only in a development browser's console (main.tsx, DEV only), so a change
 * that broke one was seen by whoever happened to open the console. esbuild bundles the four
 * modules (they are plain TypeScript, no React) and node runs them.
 */
import { build } from "esbuild";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const work = mkdtempSync(path.join(tmpdir(), "fp-self-checks-"));
const entry = path.join(work, "entry.ts");
const out = path.join(work, "self-checks.mjs");

writeFileSync(
  entry,
  `
import { runTenantResolutionSelfCheck } from "@/hooks/tenantResolution";
import { runTenantPathSelfCheck } from "@/routes/tenantPaths";
import { runTenantReconciliationSelfCheck } from "@/routes/tenantReconciliation";
import { runPermissionSelfCheck } from "@/permissions/selfCheck";
export const checks = {
  "tenant resolution": runTenantResolutionSelfCheck,
  "tenant paths": runTenantPathSelfCheck,
  "tenant reconciliation": runTenantReconciliationSelfCheck,
  "permission parity": runPermissionSelfCheck,
};
`,
);

try {
  await build({
    entryPoints: [entry],
    bundle: true,
    platform: "node",
    format: "esm",
    outfile: out,
    alias: { "@": path.join(ROOT, "src") },
    logLevel: "error",
    define: { "import.meta.env.DEV": "true", "import.meta.env.PROD": "false", "import.meta.env.MODE": '"test"' },
  });
  const { checks } = await import(pathToFileURL(out).href);
  let failed = 0;
  for (const [name, run] of Object.entries(checks)) {
    const failures = run();
    if (failures.length === 0) {
      console.log(`ok    ${name}`);
    } else {
      failed += failures.length;
      console.log(`FAIL  ${name}\n${failures.map((f) => `        - ${f}`).join("\n")}`);
    }
  }
  process.exitCode = failed ? 1 : 0;
} finally {
  rmSync(work, { recursive: true, force: true });
}
