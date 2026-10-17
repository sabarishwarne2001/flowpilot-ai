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
import { mkdtempSync, readdirSync, readFileSync, rmSync, statSync, writeFileSync } from "node:fs";
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
import { runDisplayFormatSelfCheck } from "@/utils/displayTime";
import { runBrandColourSelfCheck } from "@/utils/brandTheme";
import { runHelmMoneySelfCheck } from "@/components/helm/money";
export const checks = {
  "tenant resolution": runTenantResolutionSelfCheck,
  "tenant paths": runTenantPathSelfCheck,
  "tenant reconciliation": runTenantReconciliationSelfCheck,
  "permission parity": runPermissionSelfCheck,
  "display formatting": runDisplayFormatSelfCheck,
  "brand colours": runBrandColourSelfCheck,
  "helm money": runHelmMoneySelfCheck,
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

  // Campaign session 3 (N-064): dates, times, numbers and money go through the display layer
  // (src/utils/displayTime.ts), which reads the person's language and clock and the workspace's
  // date format. A bare toLocale*String() or new Intl.*Format() reads the browser instead.
  // Website and payments session: also Intl's other formatters and navigator.language itself.
  // formatRelativeTime passed navigator.language straight to Intl.RelativeTimeFormat, which
  // throws on a tag like "en-US@posix" and took the whole Helm cockpit down.
  const BYPASS =
    /\.toLocale(?:Date|Time)?String\(|new Intl\.(?:NumberFormat|DateTimeFormat|RelativeTimeFormat|ListFormat|PluralRules)\(|navigator\.languages?\b/;
  const ALLOWED = new Set([path.join("src", "utils", "displayTime.ts")]);
  const bypasses = [];
  const walk = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) {
        walk(full);
      } else if (/\.(ts|tsx)$/.test(name) && !name.endsWith(".d.ts")) {
        const relative = path.relative(ROOT, full);
        if (ALLOWED.has(relative)) continue;
        readFileSync(full, "utf8").split("\n").forEach((line, index) => {
          const code = line.replace(/\/\/.*$/, "").replace(/\{\/\*.*\*\/\}/, "");
          if (BYPASS.test(code)) bypasses.push(`${relative}:${index + 1}: ${line.trim()}`);
        });
      }
    }
  };
  walk(path.join(ROOT, "src"));
  if (bypasses.length === 0) {
    console.log("ok    one display layer");
  } else {
    failed += bypasses.length;
    console.log(`FAIL  one display layer\n${bypasses.map((f) => `        - ${f}`).join("\n")}`);
  }

  // Website and payments session (C10): Dodo Payments is the gateway customers see, so no screen
  // names Stripe (the dormant Stripe adapter's identifiers and comments are not shown to anyone).
  const stripeWords = [];
  const scan = (dir) => {
    for (const name of readdirSync(dir)) {
      const full = path.join(dir, name);
      if (statSync(full).isDirectory()) {
        scan(full);
      } else if (/\.(ts|tsx)$/.test(name) && !name.endsWith(".d.ts")) {
        const relative = path.relative(ROOT, full);
        readFileSync(full, "utf8").split("\n").forEach((line, index) => {
          const trimmed = line.trim();
          if (trimmed.startsWith("//") || trimmed.startsWith("*") || trimmed.startsWith("/*")) return;
          const code = line.replace(/\/\/.*$/, "").replace(/\{\/\*.*\*\/\}/, "");
          if (/\bStripe\b/.test(code)) stripeWords.push(`${relative}:${index + 1}: ${trimmed}`);
        });
      }
    }
  };
  scan(path.join(ROOT, "src"));
  if (stripeWords.length === 0) {
    console.log("ok    no Stripe on screen");
  } else {
    failed += stripeWords.length;
    console.log(`FAIL  no Stripe on screen\n${stripeWords.map((f) => `        - ${f}`).join("\n")}`);
  }
  process.exitCode = failed ? 1 : 0;
} finally {
  rmSync(work, { recursive: true, force: true });
}
