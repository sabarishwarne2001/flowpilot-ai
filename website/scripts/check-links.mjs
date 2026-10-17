// No broken internal links: every href and src on every built page that points at this site
// must resolve to a file in dist/ (or an id on its page). External links are not fetched.
import { readFile, readdir, stat } from "node:fs/promises";
import { join, relative } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));

async function* htmlFiles(dir) {
  for (const entry of await readdir(dir, { withFileTypes: true })) {
    const full = join(dir, entry.name);
    if (entry.isDirectory()) yield* htmlFiles(full);
    else if (entry.name.endsWith(".html")) yield full;
  }
}

async function exists(path) {
  try {
    return (await stat(path)).isFile();
  } catch {
    return false;
  }
}

async function resolves(pathname) {
  const clean = decodeURIComponent(pathname).replace(/\/$/, "") || "/";
  if (clean === "/") return exists(join(DIST, "index.html"));
  return (await exists(join(DIST, clean))) || (await exists(join(DIST, `${clean}.html`))) || (await exists(join(DIST, clean, "index.html")));
}

const redirects = new Set(
  (await readFile(join(DIST, "_redirects"), "utf8").catch(() => "")).split("\n").filter((l) => l && !l.startsWith("#")).map((l) => l.split(/\s+/)[0]),
);

const problems = [];
let checked = 0;
for await (const file of htmlFiles(DIST)) {
  const html = await readFile(file, "utf8");
  const ids = new Set([...html.matchAll(/\sid="([^"]+)"/g)].map((m) => m[1]));
  const page = "/" + relative(DIST, file).replace(/\.html$/, "").replace(/(^|\/)index$/, "");
  for (const m of html.matchAll(/\s(?:href|src|srcset)="([^"]+)"/g)) {
    for (const raw of m[1].split(",").map((s) => s.trim().split(/\s+/)[0])) {
      if (!raw || /^(mailto:|tel:|https?:|data:)/.test(raw)) continue;
      checked++;
      const url = new URL(raw, `http://site${page}`);
      if (url.pathname === page || (raw.startsWith("#") && url.pathname === new URL(`http://site${page}`).pathname)) {
        if (url.hash && !ids.has(decodeURIComponent(url.hash.slice(1)))) problems.push(`${page}: no element with id ${url.hash}`);
        continue;
      }
      if (redirects.has(url.pathname)) continue;
      if (!(await resolves(url.pathname))) problems.push(`${page}: broken link ${raw}`);
      else if (url.hash) {
        const target = url.pathname === "/" ? "index.html" : `${url.pathname.replace(/^\//, "")}.html`;
        const targetHtml = await readFile(join(DIST, target), "utf8").catch(() => "");
        if (targetHtml && !targetHtml.includes(`id="${decodeURIComponent(url.hash.slice(1))}"`)) problems.push(`${page}: ${raw} has no such anchor`);
      }
    }
  }
}
if (problems.length) {
  console.error(problems.join("\n"));
  console.error(`${problems.length} broken internal link(s)`);
  process.exit(1);
}
console.log(`links: ${checked} internal links, all resolve`);
