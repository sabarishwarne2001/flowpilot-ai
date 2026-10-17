/**
 * Warming Helm up before the switch is pressed (docs/helm/VISUAL.md "The switch"): when the pointer
 * enters or focuses the mode switch, the Helm layout's chunk, the remembered page's chunk and the
 * overview the layout reads start loading, so the view the circle reveals is the real page, not a
 * loading state. Each part is loaded once per page load; failures are left for the page itself to
 * report when it opens.
 */
import type { QueryClient } from "@tanstack/react-query";

import { getHelmOverview, helmKeys } from "@/services/api/helm";

const PAGES: Record<string, () => Promise<unknown>> = {
  "": () => import("@/pages/helm/HelmCockpit"),
  plan: () => import("@/pages/helm/HelmPlan"),
  mandates: () => import("@/pages/helm/HelmMandates"),
  ledger: () => import("@/pages/helm/HelmLedger"),
  "time-machine": () => import("@/pages/helm/HelmTimeMachine"),
  bank: () => import("@/pages/helm/HelmBank"),
  certificates: () => import("@/pages/helm/HelmCertificates"),
  settings: () => import("@/pages/helm/HelmSettings"),
  start: () => import("@/pages/helm/HelmStart"),
  setup: () => import("@/pages/helm/HelmFirstRun"),
};

const started = new Map<string, Promise<unknown>>();

const once = (key: string, load: () => Promise<unknown>): Promise<unknown> => {
  let promise = started.get(key);
  if (!promise) {
    promise = load().catch(() => undefined);
    started.set(key, promise);
  }
  return promise;
};

/** `helmPage` is the first segment after /helm/ of the page Helm will open ("" for the cockpit). */
export const prefetchHelm = (queryClient: QueryClient, organizationId: string, helmPage: string): Promise<unknown> => {
  const page = PAGES[helmPage] ?? PAGES[""];
  return Promise.all([
    once("layout", () => import("@/layouts/HelmLayout")),
    once(`page:${helmPage in PAGES ? helmPage : ""}`, page as () => Promise<unknown>),
    queryClient
      .prefetchQuery({ queryKey: helmKeys.overview(organizationId), queryFn: () => getHelmOverview(organizationId), staleTime: 15_000 })
      .catch(() => undefined),
  ]);
};
