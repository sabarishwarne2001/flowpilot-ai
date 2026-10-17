import type { APIRoute } from "astro";

import { absolute } from "../config/site";
import { SOLUTIONS } from "../data/solutions";

/** Every public page, for search engines. 404 is not listed. */
const PATHS = [
  "/",
  "/helm",
  "/product",
  ...SOLUTIONS.map((s) => `/solutions/${s.slug}`),
  "/pricing",
  "/security",
  "/about",
  "/contact",
  "/legal/terms",
  "/legal/privacy",
  "/legal/refunds",
  "/legal/acceptable-use",
  "/legal/subprocessors",
  "/legal/cookies",
];

export const GET: APIRoute = () => {
  const body =
    `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n` +
    PATHS.map((p) => `  <url><loc>${absolute(p)}</loc></url>`).join("\n") +
    `\n</urlset>\n`;
  return new Response(body, { headers: { "Content-Type": "application/xml; charset=utf-8" } });
};
