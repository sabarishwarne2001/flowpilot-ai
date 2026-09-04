import { lazy, Suspense } from "react";
import {
  BrowserRouter,
  Route,
  Routes,
  useParams,
  useSearchParams,
} from "react-router-dom";
import { Toaster } from "sonner";

import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { LoadingScreen } from "@/components/common/LoadingScreen";
import StepUpReauthModal from "@/components/auth/StepUpReauthModal";

import { AuthLayout } from "@/layouts/AuthLayout";
import { DashboardLayout } from "@/layouts/DashboardLayout";
import { OrganizationLayout } from "@/layouts/OrganizationLayout";

import { Assistant } from "@/pages/Assistant/Assistant";
import AssistantCanvas from "@/pages/Assistant/AssistantCanvas";

const OrganizationMembers = lazy(
  () => import("@/pages/organization/OrganizationMembers"),
);
const OrganizationApiKeys = lazy(
  () => import("@/pages/organization/OrganizationApiKeys"),
);
const OrganizationWebhooks = lazy(
  () => import("@/pages/organization/OrganizationWebhooks"),
);
const OrganizationEmailSettings = lazy(
  () => import("@/pages/organization/OrganizationEmailSettings"),
);
const OrganizationNotifications = lazy(
  () => import("@/pages/organization/OrganizationNotifications"),
);
const OrganizationSLOs = lazy(
  () => import("@/pages/organization/OrganizationSLOs"),
);
// ARCH-20. Lazy like every other organization settings surface: the
// compliance console pulls three queries and a modal that most sessions never
// open.
const OrganizationCompliance = lazy(
  () => import("@/pages/organization/OrganizationCompliance"),
);
// ARCH-21. Lazy like every other organization surface: the portal pulls
// three queries, a chart, a code-snippet explorer and an issuance modal that
// the overwhelming majority of sessions never open.
const OrganizationDeveloperPortal = lazy(
  () => import("@/pages/organization/OrganizationDeveloperPortal"),
);
// ARCH-22. Lazy like every other organization surface, so the BYOK console's
// provider cards and routing table stay out of the entry chunk.
const OrganizationBYOK = lazy(
  () => import("@/pages/organization/OrganizationBYOK"),
);
// ARCH-25. Lazy like every other organization surface. The branding console
// pulls in a colour-picker preview and an image uploader that no other page
// needs, so keeping it out of the main chunk matters more here than most.
// ARCH-26. Lazy like every other organization surface, so the analytics
// console's forms and charts stay out of the initial bundle.
const OrganizationAnalytics = lazy(
  () => import("@/pages/organization/OrganizationAnalytics"),
);
const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),
);
// ARCH-27. Lazy like every other organization surface. The catalog pulls in a
// manifest inspector that renders a DAG and a JSON viewer, which no other page
// needs and most sessions never open.
const MarketplaceCatalog = lazy(
  () => import("@/pages/marketplace/MarketplaceCatalog"),
);
// ARCH-27. The partner portal is lazy for a stronger reason than the others:
// the overwhelming majority of users are not partner members at all, and this
// bundle would otherwise ship to every one of them.
const PartnerPortal = lazy(() => import("@/pages/partner/PartnerPortal"));
const BillingHub = lazy(() => import("@/pages/billing/BillingHub"));
const CheckoutReturn = lazy(() => import("@/pages/billing/CheckoutReturn"));
const IdentityAdminHub = lazy(
  () => import("@/pages/identity/IdentityAdminHub"),
);
const AuditExplorer = lazy(() => import("@/pages/admin/AuditExplorer"));
// ARCH-18. Lazy, like every other admin surface: the margins hub pulls in
// four queries and a table the overwhelming majority of sessions never open.
const AdminMarginsHub = lazy(() => import("@/pages/admin/AdminMarginsHub"));
const ExecutionTimeline = lazy(
  () => import("@/pages/Automation/ExecutionTimeline"),
);
const VerificationReviewQueue = lazy(
  () => import("@/pages/Verification/VerificationReviewQueue"),
);

import { Login } from "@/pages/Auth/Login";
import { Register } from "@/pages/Auth/Register";
import VerifyEmail from "@/pages/Auth/VerifyEmail";
import ForgotPassword from "@/pages/Auth/ForgotPassword";
import ResetPassword from "@/pages/Auth/ResetPassword";
import InvitationAcceptPage from "@/pages/Auth/InvitationAcceptPage";
import ConfirmEmailChange from "@/pages/Auth/ConfirmEmailChange";
import { Automation } from "@/pages/Automation/Automation";
import { Dashboard } from "@/pages/Dashboard/Dashboard";
import { NotFound } from "@/pages/NotFound";
import { Notifications } from "@/pages/Notifications/Notifications";
import { WorkItemDetails } from "@/pages/WorkItems/WorkItemDetails";
import { WorkItems } from "@/pages/WorkItems/WorkItems";
import Settings from "@/pages/Settings/Settings";
import CreateOrganizationPage from "@/pages/Tenant/CreateOrganizationPage";
import NoAccess from "@/pages/Tenant/NoAccess";
import WorkspacePicker from "@/pages/Tenant/WorkspacePicker";
import CreateWorkspacePage from "@/pages/Tenant/CreateWorkspacePage";

import LegacyRouteRedirect from "@/routes/LegacyRouteRedirect";
import { PrivateRoute } from "@/routes/PrivateRoute";
import { PublicRoute } from "@/routes/PublicRoute";
import { SessionBootstrap } from "@/routes/SessionBootstrap";
import OrganizationGuard from "@/routes/OrganizationGuard";
import SuperAdminGuard from "@/routes/SuperAdminGuard";
import TenantGuard from "@/routes/TenantGuard";

import { ROUTE_PATTERNS } from "@/routes/tenantPaths";
import { ROUTES } from "@/constants/routes";

const AssistantCanvasRoute = () => {
  const { conversationId } = useParams<{ conversationId: string }>();
  const [search] = useSearchParams();

  if (!conversationId) {
    return <NotFound />;
  }

  return (
    <AssistantCanvas
      conversationId={conversationId}
      workItemId={search.get("doc")}
    />
  );
};

export default function App() {
  return (
    <ErrorBoundary>
      <Toaster
        position="top-right"
        richColors
        closeButton
        duration={4000}
      />
      <StepUpReauthModal />

      <BrowserRouter>
        <SessionBootstrap>
          <Suspense fallback={<LoadingScreen />}>
            <Routes>
              {/* Public routes */}
              <Route path={ROUTES.VERIFY_EMAIL} element={<VerifyEmail />} />
              <Route
                path={ROUTES.FORGOT_PASSWORD}
                element={<ForgotPassword />}
              />
              <Route
                path={ROUTES.RESET_PASSWORD}
                element={<ResetPassword />}
              />
              <Route
                path={ROUTES.INVITATION_ACCEPT}
                element={<InvitationAcceptPage />}
              />
              <Route
                path="/confirm-email-change"
                element={<ConfirmEmailChange />}
              />

              {/* Public auth pages */}
              <Route
                element={
                  <PublicRoute>
                    <AuthLayout />
                  </PublicRoute>
                }
              >
                <Route
                  path={ROUTES.LOGIN}
                  element={<Login />}
                />
                <Route
                  path={ROUTES.REGISTER}
                  element={<Register />}
                />
              </Route>

              {/* Authenticated, tenant-independent */}
              <Route element={<PrivateRoute />}>
                <Route
                  path={ROUTES.ONBOARDING}
                  element={<CreateOrganizationPage />}
                />
                <Route
                  path={ROUTES.WORKSPACES}
                  element={<WorkspacePicker />}
                />
                <Route
                  path={ROUTES.NO_ACCESS}
                  element={<NoAccess />}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationNewWorkspace}
                  element={<CreateWorkspacePage />}
                />

                {/* Organization-scoped routes */}
                <Route
                  path={ROUTE_PATTERNS.organizationShell}
                  element={<OrganizationGuard />}
                >
                  <Route element={<OrganizationLayout />}>
                    <Route
                      path={ROUTE_PATTERNS.organizationMembers}
                      element={<OrganizationMembers />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationApiKeys}
                      element={<OrganizationApiKeys />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationWebhooks}
                      element={<OrganizationWebhooks />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationEmail}
                      element={<OrganizationEmailSettings />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationNotifications}
                      element={<OrganizationNotifications />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationBilling}
                      element={<BillingHub />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationBillingReturn}
                      element={<CheckoutReturn />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationIdentity}
                      element={<IdentityAdminHub />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationAudit}
                      element={<AuditExplorer />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationSLOs}
                      element={<OrganizationSLOs />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationCompliance}
                      element={<OrganizationCompliance />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationDeveloper}
                      element={<OrganizationDeveloperPortal />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationBYOK}
                      element={<OrganizationBYOK />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationAnalytics}
                      element={<OrganizationAnalytics />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationBranding}
                      element={<OrganizationBranding />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.organizationMarketplace}
                      element={<MarketplaceCatalog />}
                    />
                  </Route>
                </Route>

                {/* ARCH-18 platform administration.

                    A sibling of the organization shell, never a child of it.
                    Nesting a cross-tenant page inside OrganizationGuard would
                    make platform-wide totals appear to belong to whichever
                    organization happened to be selected — the precise
                    misreading the COGS dashboard must not invite. It also has
                    no OrganizationLayout, so no tenant switcher is rendered
                    beside numbers that do not respond to it. */}
                <Route
                  path={ROUTE_PATTERNS.platformShell}
                  element={<SuperAdminGuard />}
                >
                  <Route
                    path={ROUTE_PATTERNS.platformMargins}
                    element={<AdminMarginsHub />}
                  />
                </Route>

                {/* ARCH-27 partner portal.

                    A sibling of the organization shell, never a child of it —
                    the same decision ARCH-18 made for /admin. A partner reads
                    across a BOOK of organizations, so nesting this inside
                    OrganizationGuard would render a tenant switcher beside
                    figures that do not respond to it, and would make a
                    book-wide total appear to belong to whichever organization
                    happened to be selected.

                    It is behind PrivateRoute, not SuperAdminGuard: partner
                    membership is authorized server-side by
                    tenancy_service.require_membership, which returns 404 for a
                    non-member so the route is not a partner enumeration
                    oracle. */}
                <Route
                  path={ROUTE_PATTERNS.partnerPortalShell}
                  element={<PartnerPortal />}
                />

                {/* Legacy redirects */}
                <Route path="/" element={<LegacyRouteRedirect />} />
                <Route path="/work-items/*" element={<LegacyRouteRedirect />} />
                <Route path="/assistant/*" element={<LegacyRouteRedirect />} />
                <Route path="/automation/*" element={<LegacyRouteRedirect />} />
                <Route
                  path="/notifications/*"
                  element={<LegacyRouteRedirect />}
                />
                <Route path="/settings/*" element={<LegacyRouteRedirect />} />
                <Route path="/profile/*" element={<LegacyRouteRedirect />} />
                <Route path="/account/*" element={<LegacyRouteRedirect />} />

                {/* Workspace-scoped shell */}
                <Route
                  path={ROUTE_PATTERNS.workspaceShell}
                  element={<TenantGuard />}
                >
                  <Route element={<DashboardLayout />}>
                    <Route
                      index
                      element={<Dashboard />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceWorkItems}
                      element={<WorkItems />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceWorkItemDetails}
                      element={<WorkItemDetails />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceAssistant}
                      element={<Assistant />}
                    />
                    <Route
                      path={`${ROUTE_PATTERNS.workspaceAssistant}/c/:conversationId`}
                      element={<AssistantCanvasRoute />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceAutomation}
                      element={<Automation />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceAutomationTimeline}
                      element={<ExecutionTimeline />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceVerification}
                      element={<VerificationReviewQueue />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceNotifications}
                      element={<Notifications />}
                    />
                    <Route
                      path={ROUTE_PATTERNS.workspaceSettings}
                      element={<Settings />}
                    />
                  </Route>
                </Route>
              </Route>

              {/* 404 */}
              <Route
                path={ROUTES.NOT_FOUND}
                element={<NotFound />}
              />
            </Routes>
          </Suspense>
        </SessionBootstrap>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
