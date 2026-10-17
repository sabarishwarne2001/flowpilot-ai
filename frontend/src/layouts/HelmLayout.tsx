import React, { Suspense, useMemo, useRef, useState } from "react";
import { Link, Navigate, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  BadgeCheck,
  BookOpen,
  ChevronRight,
  Gauge,
  History,
  Landmark,
  Lock,
  Menu,
  Route as RouteIcon,
  ScrollText,
  Settings2,
  ShieldCheck,
  X,
} from "lucide-react";

import { HelmProvider, useHelm } from "@/components/helm/HelmContext";
import { HelmTour } from "@/components/helm/HelmTour";
import { ModeSwitch } from "@/components/helm/ModeSwitch";
import { HelmMark } from "@/components/helm/ui/HelmMark";
import { HelmSkeleton } from "@/components/helm/ui/HelmSkeleton";
import { useSpotlight } from "@/components/helm/ui/useSpotlight";
import ThemeToggle from "@/components/layout/ThemeToggle";
import OrganizationNotificationBell from "@/components/notification/OrganizationNotificationBell";
import DunningBanner from "@/components/billing/DunningBanner";
import TrialBanner from "@/components/billing/TrialBanner";
import { DisplayPreferencesBoundary } from "@/components/common/DisplayPreferencesBoundary";
import { LoadFailed } from "@/components/common/LoadFailed";
import { RouteErrorBoundary } from "@/components/common/RouteErrorBoundary";
import { useDocumentTitle } from "@/hooks/useDocumentTitle";
import { useTenant } from "@/hooks/useTenant";
import { useResolvedOrganization } from "@/routes/OrganizationGuard";
import { helmPath, organizationBillingPath, workspaceDashboardPath, type HelmPage } from "@/routes/tenantPaths";
import { getHelmOverview, helmKeys } from "@/services/api/helm";
import "@/styles/helm.css";

interface HelmNavItem {
  readonly page: HelmPage;
  readonly name: string;
  readonly icon: React.ComponentType<{ className?: string; "aria-hidden"?: boolean | "true" }>;
}

/** Helm is a mode with its own sidebar, not one more item in Documents' sidebar (brief §10.1). */
export const HELM_NAV: readonly HelmNavItem[] = [
  { page: "", name: "Cockpit", icon: Gauge },
  { page: "plan", name: "Plan", icon: RouteIcon },
  { page: "mandates", name: "Mandates", icon: ScrollText },
  { page: "ledger", name: "Promise ledger", icon: BookOpen },
  { page: "time-machine", name: "Time machine", icon: History },
  { page: "bank", name: "Bank statements", icon: Landmark },
  { page: "certificates", name: "Certificates", icon: BadgeCheck },
  { page: "settings", name: "Settings", icon: Settings2 },
];

const pageOf = (pathname: string, orgSlug: string): HelmPage => {
  const root = helmPath(orgSlug);
  const rest = pathname.startsWith(root) ? pathname.slice(root.length).replace(/^\/+/, "") : "";
  return (rest.split("/")[0] ?? "") as HelmPage;
};

/**
 * Helm's sidebar. Every page but Start shows a book (a workspace's promises and plan), so before one
 * is chosen the pages are listed but closed: choosing one says why and points at Start, instead of
 * navigating to a page that would send the person straight back (the "blink" this replaced).
 */
const HelmSidebar: React.FC<{ readonly onNavigate?: () => void }> = ({ onNavigate }) => {
  const { orgSlug, organizationName, withBook, access, book } = useHelm();
  const { pathname } = useLocation();
  const current = pageOf(pathname, orgSlug);
  const [explained, setExplained] = useState(false);
  const ready = book !== null;
  return (
    <nav aria-label="Helm" className="flex h-full flex-col gap-1 p-3">
      <div className="mb-4 flex items-center gap-2.5 px-2 pt-1.5">
        <span className="helm-accent flex h-9 w-9 shrink-0 items-center justify-center rounded-xl border border-[hsl(var(--helm-accent)/0.35)] bg-[hsl(var(--helm-accent)/0.1)] shadow-[0_0_24px_-6px_hsl(var(--helm-accent)/0.7)]">
          <HelmMark size={22} />
        </span>
        <div className="min-w-0">
          <p className="text-[15px] font-semibold leading-tight tracking-tight">Helm</p>
          <p className="helm-muted truncate text-xs">{organizationName}</p>
        </div>
      </div>
      <p className="helm-label px-3 pb-1">{ready ? "Bridge" : "Choose a book first"}</p>
      {HELM_NAV.map((item) => {
        const Icon = item.icon;
        if (!ready) {
          return (
            <a
              key={item.page || "cockpit"}
              role="link"
              tabIndex={0}
              aria-disabled="true"
              aria-describedby="helm-nav-closed"
              className="helm-nav-link"
              data-testid={`helm-nav-${item.page || "cockpit"}`}
              onClick={(event) => {
                event.preventDefault();
                setExplained(true);
              }}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setExplained(true);
                }
              }}
            >
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              {item.name}
              <Lock className="helm-faint ml-auto h-3 w-3" aria-hidden="true" />
            </a>
          );
        }
        const active = current === item.page;
        return (
          <Link
            key={item.page || "cockpit"}
            to={withBook(helmPath(orgSlug, item.page))}
            className="helm-nav-link"
            aria-current={active ? "page" : undefined}
            onClick={onNavigate}
            data-testid={`helm-nav-${item.page || "cockpit"}`}
          >
            <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
            {item.name}
          </Link>
        );
      })}
      {!ready ? (
        <p
          id="helm-nav-closed"
          className={`mx-1 mt-2 rounded-xl px-3 py-2 text-xs leading-relaxed ${explained ? "helm-panel-raised" : "helm-muted"}`}
          role="status"
          data-testid="helm-nav-closed"
        >
          {explained
            ? "These pages show one book: a workspace's promises and plan. Open the sample company or start Helm on a workspace here on Start, and they open."
            : "These pages open once you choose a book on Start."}
        </p>
      ) : null}
      <div className="mt-auto space-y-3 px-1 pb-1 pt-4">
        <Link
          to={helmPath(orgSlug, "start")}
          className="helm-accent block px-2 text-xs font-medium hover:underline"
          aria-current={current === "start" ? "page" : undefined}
          onClick={onNavigate}
        >
          Another workspace, or the sample company
        </Link>
        {access.preview ? (
          <p className="helm-panel-raised helm-muted flex items-start gap-2 p-3 text-xs leading-relaxed">
            <Lock className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            Helm preview: the sample company and one backtest a week on your own documents.
          </p>
        ) : (
          <p className="helm-panel-raised helm-muted flex items-start gap-2 p-3 text-xs leading-relaxed">
            <ShieldCheck className="helm-accent mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            Helm never moves money. Payments go through your bank.
          </p>
        )}
      </div>
    </nav>
  );
};

const PreviewBanner: React.FC = () => {
  const { access, orgSlug, role } = useHelm();
  if (!access.preview) {
    return null;
  }
  return (
    <div className="helm-hairline helm-topbar flex flex-wrap items-center gap-x-3 gap-y-1 border-b px-4 py-2 text-xs sm:px-6">
      <span className="helm-chip">Preview</span>
      <span className="helm-muted">
        Helm is included on the Business and Enterprise plans. On yours you can explore the sample company and run one
        backtest a week on your own documents.
      </span>
      {role === "OWNER" || role === "BILLING" ? (
        <Link to={organizationBillingPath(orgSlug)} className="helm-accent font-semibold underline-offset-2 hover:underline">
          See plans
        </Link>
      ) : null}
    </div>
  );
};

const BookSwitcher: React.FC = () => {
  const { books, book, selectBook } = useHelm();
  if (books.length < 2 || !book) {
    return null;
  }
  return (
    <label className="hidden items-center gap-2 text-xs md:flex">
      <span className="helm-label">Book</span>
      <select
        className="helm-input h-8 min-h-0 w-44 py-0 text-[13px]"
        value={book.id}
        onChange={(event) => selectBook(event.target.value)}
      >
        {books.map((candidate) => (
          <option key={candidate.id} value={candidate.id}>
            {candidate.kind === "SAMPLE" ? `${candidate.name} (sample)` : candidate.name}
          </option>
        ))}
      </select>
    </label>
  );
};

const HelmChrome: React.FC = () => {
  const { orgSlug, organizationId, organizationName, role, book, withBook } = useHelm();
  const { pathname } = useLocation();
  const { state } = useTenant();
  const [mobileOpen, setMobileOpen] = useState(false);
  const shell = useRef<HTMLDivElement>(null);
  useSpotlight(shell);
  const page = pageOf(pathname, orgSlug);
  const pageName =
    page === "start" ? "Start" : page === "setup" ? "Setup" : HELM_NAV.find((item) => item.page === page)?.name ?? "Cockpit";
  useDocumentTitle(pageName, `Helm · ${organizationName}`);

  const documentsPath =
    state.status === "ready" ? workspaceDashboardPath(orgSlug, state.workspace.slug) : "/workspaces";

  // Every page but Start needs a book; without one, Start is where Helm begins.
  if (!book && page !== "start") {
    return <Navigate to={helmPath(orgSlug, "start")} replace />;
  }

  return (
    <div ref={shell} className="helm helm-shell flex h-screen w-full overflow-hidden" data-testid="helm-shell">
      <div className="helm-grain" aria-hidden="true" />
      <aside className="helm-rail helm-hairline hidden h-screen w-[256px] min-h-0 shrink-0 flex-col border-r lg:flex">
        <HelmSidebar />
      </aside>

      {mobileOpen ? (
        <div className="fixed inset-0 z-40 flex lg:hidden">
          <div className="helm helm-rail helm-hairline flex h-full w-[280px] max-w-[85vw] min-h-0 flex-col border-r bg-[hsl(var(--helm-bg))]">
            <HelmSidebar onNavigate={() => setMobileOpen(false)} />
          </div>
          <div className="flex-1 bg-black/60 backdrop-blur-sm" onClick={() => setMobileOpen(false)} aria-hidden="true" />
        </div>
      ) : null}

      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="helm-topbar helm-hairline relative z-20 flex h-16 shrink-0 items-center justify-between gap-2 border-b px-3 sm:px-6">
          <div className="flex min-w-0 items-center gap-2">
            <button
              type="button"
              className="flex h-9 w-9 items-center justify-center rounded-full border border-[hsl(var(--helm-rim-strong))] lg:hidden"
              onClick={() => setMobileOpen((open) => !open)}
              aria-label="Toggle Helm navigation"
              aria-expanded={mobileOpen}
            >
              {mobileOpen ? <X className="h-5 w-5" aria-hidden="true" /> : <Menu className="h-5 w-5" aria-hidden="true" />}
            </button>
            <nav aria-label="Breadcrumb" className="min-w-0">
              <ol className="flex min-w-0 items-center gap-1 text-[13px]">
                <li className="helm-muted hidden min-w-0 truncate md:block">{organizationName}</li>
                <li aria-hidden="true" className="helm-muted hidden md:block">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
                <li className="hidden min-w-0 truncate sm:block">
                  {book ? (
                    <Link to={withBook(helmPath(orgSlug))} className="helm-muted hover:underline">
                      {book.name}
                    </Link>
                  ) : (
                    <span className="helm-muted">Helm</span>
                  )}
                </li>
                <li aria-hidden="true" className="helm-muted hidden sm:block">
                  <ChevronRight className="h-3.5 w-3.5" />
                </li>
                <li className="min-w-0 truncate font-medium" aria-current="page">
                  {pageName}
                </li>
              </ol>
            </nav>
          </div>
          <div className="flex shrink-0 items-center gap-1.5 sm:gap-2">
            <BookSwitcher />
            <ModeSwitch mode="helm" organizationId={organizationId} orgSlug={orgSlug} documentsPath={documentsPath} />
            <ThemeToggle />
            <OrganizationNotificationBell organizationId={organizationId} orgSlug={orgSlug} />
          </div>
        </header>

        {/* Every Helm role reads billing (owners, admins, billing), as in OrganizationLayout. */}
        <DunningBanner organizationId={organizationId} canManageBilling={role === "OWNER"} />
        <TrialBanner organizationId={organizationId} organizationSlug={orgSlug} canChoosePlan={role === "OWNER"} />
        <PreviewBanner />

        <main className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 sm:p-6 lg:px-9 lg:py-8">
          <DisplayPreferencesBoundary>
            <RouteErrorBoundary>
              <Suspense fallback={<HelmSkeleton label="Loading…" />}>
                <Outlet />
              </Suspense>
            </RouteErrorBoundary>
          </DisplayPreferencesBoundary>
        </main>
        <HelmTour />
      </div>
    </div>
  );
};

export const HelmLayout: React.FC = () => {
  const { organization, organizationId, organizationRole } = useResolvedOrganization();
  const role = String(organizationRole ?? "").toUpperCase();
  const overview = useQuery({
    queryKey: helmKeys.overview(organizationId),
    queryFn: () => getHelmOverview(organizationId),
    staleTime: 15_000,
  });

  const body = useMemo(() => {
    if (overview.isLoading) {
      return (
        <div className="helm helm-shell flex h-screen flex-col items-center justify-center gap-3" role="status" aria-live="polite">
          <HelmMark size={44} spinning className="helm-accent" />
          <span className="helm-muted text-sm">Opening Helm…</span>
        </div>
      );
    }
    if (overview.isError || !overview.data) {
      return (
        <div className="helm helm-shell flex h-screen items-center justify-center p-6">
          <LoadFailed what="Helm" onRetry={() => void overview.refetch()} />
        </div>
      );
    }
    return (
      <HelmProvider
        organizationId={organizationId}
        orgSlug={organization.organization_slug}
        organizationName={organization.organization_name}
        role={role}
        overview={overview.data}
      >
        <HelmChrome />
      </HelmProvider>
    );
  }, [organization.organization_name, organization.organization_slug, organizationId, overview, role]);

  return body;
};

export default HelmLayout;
