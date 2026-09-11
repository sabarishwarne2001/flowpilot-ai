import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";

/**
 * ARCH-30 Tranche 1 (T4-F5) — a production bundle must call its API same-origin.
 *
 * WHY THIS IS A BUILD FAILURE AND NOT A WARNING
 * =============================================
 *
 * ARCH-25 selects a tenant from the Host header. On a tenant custom domain,
 * Caddy serves this bundle AND proxies /api/* with the tenant's Host intact, so
 * a relative `/api/v1` call from `ai.acme.com` reaches the backend as Acme.
 *
 * A bundle built with an absolute `VITE_API_URL` sends every call to that
 * absolute host instead. On the platform origin nothing looks wrong. On every
 * tenant domain the branding manifest silently returns FlowPilot's defaults,
 * and the refresh cookie scoped to the tenant's origin is never presented. No
 * request errors, so no log, test or dashboard would surface it; the first
 * report would be a customer's screenshot. A warning in a CI log is the same
 * silence with extra steps.
 *
 * `client.ts` now defaults to `/api/v1` in production; this makes the remaining
 * way to get it wrong — setting an absolute URL — refuse to produce a bundle.
 *
 * THE ESCAPE HATCH
 * ================
 *
 * A deployment that genuinely serves the API from another origin, and has no
 * tenant custom domains, sets VITE_ALLOW_CROSS_ORIGIN_API=true. It is a
 * separate variable so that choosing it is a decision someone typed, not a
 * side effect of copying .env.example.
 *
 * Development builds and the dev server are untouched: Vite and uvicorn run on
 * different ports, and the absolute default is correct there.
 */
function assertSameOriginApi(mode: string, command: string): void {
  if (command !== "build" || mode !== "production") {
    return;
  }

  // Empty prefix: loadEnv then returns process.env as well as .env files, so a
  // value exported in the CI shell is caught the same as one in .env.production.
  const env = loadEnv(mode, process.cwd(), "");
  const configured = env.VITE_API_URL;

  if (!configured) {
    return;
  }

  const isAbsolute = /^[a-z][a-z0-9+.-]*:\/\//i.test(configured) || configured.startsWith("//");
  if (!isAbsolute) {
    return;
  }

  if (env.VITE_ALLOW_CROSS_ORIGIN_API === "true") {
    return;
  }

  throw new Error(
    [
      `VITE_API_URL is absolute (${configured}) in a production build.`,
      "Tenant custom domains (ARCH-25) require the SPA to call its API same-origin:",
      "unset VITE_API_URL (the production default is /api/v1) or set it to a relative path.",
      "If this deployment has no tenant custom domains and serves the API cross-origin",
      "on purpose, set VITE_ALLOW_CROSS_ORIGIN_API=true.",
    ].join("\n"),
  );
}

export default defineConfig(({ mode, command }) => {
  assertSameOriginApi(mode, command);

  return {
    plugins: [react()],

    resolve: {
      alias: {
        "@": path.resolve(__dirname, "src"),
      },
    },

    server: {
      host: true,
      port: 3000,
      strictPort: true,
      open: true,
    },

    preview: {
      host: true,
      port: 3000,
      strictPort: true,
    },

    build: {
      sourcemap: true,
      chunkSizeWarningLimit: 1000,

      rollupOptions: {
        output: {
          manualChunks: {
            pdf: ["pdfjs-dist"],
            markdown: ["marked", "dompurify", "highlight.js"],
          },
        },
      },
    },
  };
});
