#!/usr/bin/env node
/**
 * How much a browser downloads before the first screen renders (session 4, §B.1).
 *
 *   npm run build && node scripts/measure-first-load.mjs
 *   node scripts/measure-first-load.mjs --plus Login      # also: what the /login page adds
 *   node scripts/measure-first-load.mjs --max-kb 200      # exit 1 above 200 KB gzipped
 *
 * "First load" is everything dist/index.html makes the browser fetch up front: its scripts, every
 * modulepreload and stylesheet link, and (in case Vite ever stops listing one) every chunk those
 * scripts import statically. Lazy `import()` targets are not first load; they arrive with the page
 * that needs them, and `--plus <Chunk>[,<Chunk>]` adds a lazy chunk (by its name before the hash)
 * with its static imports, to show a page's full cost. Sizes are gzip level 9, close to what Caddy
 * sends over the wire.
 */
import { existsSync, readdirSync, readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { gzipSync } from "node:zlib";

const ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const DIST = join(ROOT, "dist");
const option = (name) => {
  const at = process.argv.indexOf(name);
  return at > 0 ? process.argv[at + 1] : undefined;
};
const maxKb = option("--max-kb") === undefined ? null : Number(option("--max-kb"));
const plus = (option("--plus") ?? "").split(",").filter(Boolean);

if (!existsSync(join(DIST, "index.html"))) {
  console.error("dist/index.html does not exist; run `npm run build` first");
  process.exit(1);
}

const kb = (bytes) => (bytes / 1024).toFixed(1);
const sizes = new Map();
function measure(rel) {
  if (!sizes.has(rel)) {
    const body = readFileSync(join(DIST, rel));
    sizes.set(rel, { raw: body.length, gz: gzipSync(body, { level: 9 }).length });
  }
  return sizes.get(rel);
}

// Static imports only: `import{a}from"./x.js"`, `import"./x.js"`, `export{a}from"./x.js"`.
// `import("./x.js")` is lazy and deliberately not followed.
const STATIC_IMPORT = /\b(?:import|export)\s*(?:[\w$*{}\s,]*?\bfrom\s*)?["'](\.{1,2}\/[^"']+\.js)["']/g;
function closure(start, exclude = new Set()) {
  const seen = new Set();
  const queue = [...start];
  while (queue.length) {
    const rel = queue.shift();
    if (seen.has(rel) || exclude.has(rel) || !existsSync(join(DIST, rel))) continue;
    seen.add(rel);
    if (rel.endsWith(".js")) {
      const code = readFileSync(join(DIST, rel), "utf8");
      for (const [, spec] of code.matchAll(STATIC_IMPORT)) {
        queue.push(join(dirname(rel), spec).replace(/\\/g, "/"));
      }
    }
  }
  return seen;
}

const row = (name, s) => `${name.padEnd(52)} ${kb(s.raw).padStart(9)} ${kb(s.gz).padStart(9)}`;
const header = `${"file".padEnd(52)} ${"raw KB".padStart(9)} ${"gzip KB".padStart(9)}`;
function table(title, files) {
  const rows = [...files].map((rel) => [rel, measure(rel)]).sort((a, b) => b[1].gz - a[1].gz);
  const total = rows.reduce((sum, [, s]) => ({ raw: sum.raw + s.raw, gz: sum.gz + s.gz }), { raw: 0, gz: 0 });
  console.log(`${title}\n\n${header}`);
  for (const [rel, s] of rows) console.log(row(rel, s));
  console.log(row("TOTAL", total));
  return total;
}

const html = readFileSync(join(DIST, "index.html"), "utf8");
const local = (url) => (/^(?:[a-z]+:)?\/\//i.test(url) ? null : url.replace(/^\//, ""));
const linked = [];
for (const [tag] of html.matchAll(/<(?:script|link)\b[^>]*>/gi)) {
  const attr = (name) => tag.match(new RegExp(`\\b${name}\\s*=\\s*["']([^"']+)["']`, "i"))?.[1];
  const rel = (attr("rel") ?? "").toLowerCase();
  const url = tag.startsWith("<script") ? attr("src") : ["modulepreload", "stylesheet"].includes(rel) ? attr("href") : null;
  if (url && local(url)) linked.push(local(url));
}

const firstLoad = closure(linked);
const total = table("First load (dist/index.html and its static imports)", firstLoad);

const assetNames = readdirSync(join(DIST, "assets")).filter((name) => /\.(js|css)$/.test(name));
if (plus.length) {
  const chunks = plus.map((name) => {
    const match = assetNames.find((file) => file.endsWith(".js") && file.replace(/-[\w-]{8}\.js$/, "") === name);
    if (!match) {
      console.error(`\nNo chunk named ${name} in dist/assets`);
      process.exit(1);
    }
    return `assets/${match}`;
  });
  // A lazy chunk's CSS is injected by Vite at runtime; it shares the chunk's name.
  const css = plus.flatMap((name) => assetNames.filter((file) => file.endsWith(".css") && file.replace(/-[\w-]{8}\.css$/, "") === name));
  const extra = table(`\nAdded by ${plus.join(" + ")} (lazy, with static imports not already loaded)`, [
    ...closure(chunks, firstLoad),
    ...css.map((file) => `assets/${file}`),
  ]);
  console.log(row(`FIRST LOAD + ${plus.join(" + ")}`, { raw: total.raw + extra.raw, gz: total.gz + extra.gz }));
}

const largest = assetNames
  .map((name) => [`assets/${name}`, measure(`assets/${name}`)])
  .sort((a, b) => b[1].gz - a[1].gz)
  .slice(0, 10);
console.log(`\nLargest 10 chunks overall (* = first load)\n\n${header}`);
for (const [rel, s] of largest) console.log(row(`${firstLoad.has(rel) ? "* " : "  "}${rel}`, s));

if (maxKb !== null && total.gz / 1024 > maxKb) {
  console.error(`\nFirst load is ${kb(total.gz)} KB gzipped, over the ${maxKb} KB budget`);
  process.exit(1);
}
