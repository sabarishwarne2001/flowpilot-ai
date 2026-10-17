// @ts-check
import { defineConfig } from "astro/config";
import { fileURLToPath } from "node:url";

import { siteUrl } from "./src/config/env.mjs";

/**
 * Servabo's website: static HTML, no server. Built for Cloudflare Pages (docs/website/DEPLOY.md)
 * or any static host (the Caddy site block in backend/deploy/Caddyfile.website).
 *
 * Two settings keep the strict Content-Security-Policy in public/_headers honest:
 * `inlineStylesheets: "never"` and `assetsInlineLimit: 0`, so no style or script is ever inlined
 * into a page (the policy allows only files from this origin).
 */
export default defineConfig({
  site: siteUrl(),
  output: "static",
  trailingSlash: "never",
  build: {
    // /pricing.html, served at /pricing by Cloudflare Pages and by Caddy's try_files.
    format: "file",
    inlineStylesheets: "never",
  },
  // No prefetch, no view transitions, no toolbar: nothing the policy would have to allow.
  prefetch: false,
  devToolbar: { enabled: false },
  vite: {
    build: { assetsInlineLimit: 0 },
    server: {
      fs: {
        // The brand (tokens, mark) and Helm's identity are shared with the app.
        allow: [
          fileURLToPath(new URL(".", import.meta.url)),
          fileURLToPath(new URL("../brand", import.meta.url)),
          fileURLToPath(new URL("../frontend/src/styles", import.meta.url)),
        ],
      },
    },
  },
});
