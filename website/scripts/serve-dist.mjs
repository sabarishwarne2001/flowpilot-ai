// A tiny static server for dist/, with the same URL rules as Cloudflare Pages and Caddy:
// /pricing serves pricing.html, a missing path serves 404.html with status 404, and every
// response carries the headers from public/_headers (so the checks run under the real CSP).
import { createServer } from "node:http";
import { readFile, stat } from "node:fs/promises";
import { extname, join, normalize } from "node:path";
import { fileURLToPath } from "node:url";

const DIST = fileURLToPath(new URL("../dist/", import.meta.url));
const TYPES = {
  ".html": "text/html; charset=utf-8", ".css": "text/css", ".js": "text/javascript", ".svg": "image/svg+xml",
  ".png": "image/png", ".webp": "image/webp", ".avif": "image/avif", ".woff2": "font/woff2", ".xml": "application/xml",
  ".txt": "text/plain; charset=utf-8", ".json": "application/json", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json",
};

async function globalHeaders() {
  const text = await readFile(join(DIST, "_headers"), "utf8");
  const headers = {};
  let inGlobal = false;
  for (const line of text.split("\n")) {
    if (/^\S/.test(line)) inGlobal = line.trim() === "/*";
    else if (inGlobal && line.includes(":")) {
      const [k, ...v] = line.trim().split(":");
      headers[k.trim()] = v.join(":").trim();
    }
  }
  return headers;
}

async function file(path) {
  try {
    const s = await stat(path);
    return s.isFile() ? path : null;
  } catch {
    return null;
  }
}

export async function serve(port = 4322) {
  const headers = await globalHeaders();
  const server = createServer(async (req, res) => {
    const url = new URL(req.url ?? "/", "http://localhost");
    const clean = normalize(decodeURIComponent(url.pathname)).replace(/^(\.\.[/\\])+/, "");
    const candidates = clean.endsWith("/") ? [join(DIST, clean, "index.html")] : [join(DIST, clean), join(DIST, `${clean}.html`), join(DIST, clean, "index.html")];
    let found = null;
    for (const c of candidates) if ((found = await file(c))) break;
    const status = found ? 200 : 404;
    const path = found ?? join(DIST, "404.html");
    res.writeHead(status, { ...headers, "Content-Type": TYPES[extname(path)] ?? "application/octet-stream" });
    res.end(await readFile(path));
  });
  await new Promise((resolve) => server.listen(port, "127.0.0.1", resolve));
  return { url: `http://127.0.0.1:${port}`, close: () => new Promise((r) => server.close(r)) };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const { url } = await serve(Number(process.env.PORT ?? 4322));
  console.log(`serving dist/ at ${url}`);
}
