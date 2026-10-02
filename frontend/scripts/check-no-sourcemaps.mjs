#!/usr/bin/env node
/**
 * F-038 — a production bundle must not ship source maps.
 *
 *   node scripts/check-no-sourcemaps.mjs            # checks the Vite config
 *   node scripts/check-no-sourcemaps.mjs --dist     # also checks ./dist after a build
 *
 * A source map (index-<hash>.js.map) is the application's original TypeScript
 * source, comments included, addressable by anyone who can read the bundle. Caddy
 * serves the whole dist folder, so a map that is built is a map that is public.
 * The check exits 1 with the reason; CI runs it after `npm run build`.
 */
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { loadConfigFromFile } from "vite";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const problems = [];

const loaded = await loadConfigFromFile({ command: "build", mode: "production" }, "vite.config.ts", ROOT);
const sourcemap = loaded?.config?.build?.sourcemap;
if (sourcemap) {
  problems.push(`vite.config.ts build.sourcemap is ${JSON.stringify(sourcemap)}; it must be false for a production build`);
}

if (process.argv.includes("--dist")) {
  const dist = join(ROOT, "dist");
  if (!existsSync(dist)) {
    problems.push("dist/ does not exist; run `npm run build` first");
  } else {
    const walk = (dir) =>
      readdirSync(dir).flatMap((name) => {
        const path = join(dir, name);
        return statSync(path).isDirectory() ? walk(path) : [path];
      });
    for (const file of walk(dist)) {
      if (file.endsWith(".map")) problems.push(`dist contains a source map: ${file.slice(ROOT.length + 1)}`);
      else if (/\.(js|css|mjs)$/.test(file) && /sourceMappingURL=/.test(readFileSync(file, "utf8").slice(-400))) {
        problems.push(`dist file points at a source map: ${file.slice(ROOT.length + 1)}`);
      }
    }
  }
}

if (problems.length) {
  console.error("Source maps would be public:\n  - " + problems.join("\n  - "));
  process.exit(1);
}
console.log("No source maps in the production build configuration" + (process.argv.includes("--dist") ? " or in dist/." : "."));
