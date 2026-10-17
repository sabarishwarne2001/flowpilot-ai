// Every page of the built site, read from dist/sitemap.xml (plus the 404 page).
import { readFile } from "node:fs/promises";

export async function pages() {
  const xml = await readFile(new URL("../dist/sitemap.xml", import.meta.url), "utf8");
  const paths = [...xml.matchAll(/<loc>([^<]+)<\/loc>/g)].map((m) => new URL(m[1]).pathname);
  return [...paths, "/this-page-does-not-exist"];
}
