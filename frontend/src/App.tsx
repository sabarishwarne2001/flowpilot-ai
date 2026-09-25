import { lazy, Suspense } from "react";
import { UnsavedChangesDialogHost } from "@/components/common/UnsavedChangesDialogHost";
import {
  Navigate,
  Route,
  Routes,
  useParams,
  useSearchParams,
  RouterProvider,
  createBrowserRouter,
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
const OrganizationGeneral = lazy(
  () => import("@/pages/organization/OrganizationGeneral"),
);
const OrganizationNotifications = lazy(
  () => import("@/pages/organization/OrganizationNotifications"),
);
const OrganizationSLOs = lazy(
  () => import("@/pages/organization/OrganizationSLOs"),
);
const OrganizationCompliance = lazy(
  () => import("@/pages/organization/OrganizationCompliance"),
);
const OrganizationDeveloperPortal = lazy(
  () => import("@/pages/organization/OrganizationDeveloperPortal"),
);
const OrganizationBYOK = lazy(
  () => import("@/pages/organization/OrganizationBYOK"),
);
const OrganizationAnalytics = lazy(
  () => import("@/pages/organization/OrganizationAnalytics"),
);
const OrganizationBranding = lazy(
  () => import("@/pages/organization/OrganizationBranding"),
);
// ARCH35-S3:autonomy-route
const AutonomySettings = lazy(
  () => import("@/pages/autonomy/AutonomySettings"),
);
const MarketplaceCatalog = lazy(
  () => import("@/pages/marketplace/MarketplaceCatalog"),
);
const PartnerPortal = lazy(() => import("@/pages/partner/PartnerPortal"));
const BillingHub = lazy(() => import("@/pages/billing/BillingHub"));
const CheckoutReturn = lazy(() => import("@/pages/billing/CheckoutReturn"));
const IdentityAdminHub = lazy(
  () => import("@/pages/identity/IdentityAdminHub"),
);
const AuditExplorer = lazy(() => import("@/pages/admin/AuditExplorer"));
const AdminMarginsHub = lazy(() => import("@/pages/admin/AdminMarginsHub"));
const PlatformLayout = lazy(() => import("@/layouts/PlatformLayout"));
const ExecutionTimeline = lazy(
  () => import("@/pages/Automation/ExecutionTimeline"),
);
// ARCH40-S2:review-hub-route. The unified review hub replaces the
// extraction-only queue on this route; the queue lives on inside the hub as
// its field-level workbench.
const ReviewHub = lazy(
  () => import("@/pages/Verification/ReviewHub"),
);
// ARCH34-S3:radar-route
const ForensicAuditRadar = lazy(
  () => import("@/pages/radar/ForensicAuditRadar"),
);
// ARCH42-S2:entity-routes
const Entities = lazy(() => import("@/pages/entities/Entities"));
const Entity360 = lazy(() => import("@/pages/entities/Entity360"));
// ARCH43-S2:lazy-pages
const Cases = lazy(() => import("@/pages/cases/Cases"));
const CaseDetailPage = lazy(() => import("@/pages/cases/CaseDetail"));
const PacketSplits = lazy(() => import("@/pages/packets/PacketSplits"));
const SplitReview = lazy(() => import("@/pages/packets/SplitReview"));
const DocumentRequestUpload = lazy(() => import("@/pages/public/DocumentRequestUpload"));
// ARCH44-S2:lazy-pages
const Tables = lazy(() => import("@/pages/tables/Tables"));
const TableViewer = lazy(() => import("@/pages/tables/TableViewer"));
// ARCH45-S2:lazy-pages
const Corroborations = lazy(() => import("@/pages/corroboration/Corroborations"));
const CorroborationRun = lazy(() => import("@/pages/corroboration/CorroborationRun"));
// ARCH46-S2:lazy-pages
const Obligations = lazy(() => import("@/pages/obligations/Obligations"));
const ObligationDetail = lazy(() => import("@/pages/obligations/ObligationDetail"));
// ARCH41-S3:extraction-memory-route
const ExtractionMemory = lazy(
  () => import("@/pages/extractionMemory/ExtractionMemory"),
);
// ARCH36-S1:assertions-route
const AssertionReviewPage = lazy(
  () => import("@/pages/Assertions/AssertionReviewPage"),
);
const RedactionStudio = lazy(
  () => import("@/pages/redaction/RedactionStudio"),
);
const ProcurementCaseQueue = lazy(
  () => import("@/pages/procurement/CaseQueue"),
);
const ThreeWayComparison = lazy(
  () => import("@/pages/procurement/ThreeWayComparison"),
);
const TolerancePolicyEditor = lazy(
  () => import("@/pages/procurement/TolerancePolicyEditor"),
);

import { Login } from "@/pages/Auth/Login";
import { SsoComplete } from "@/pages/Auth/SsoComplete";
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

/**
 * HARDENING-FINAL:nav-guard. The route tree, served by a data router so
 * `useBlocker` works: unsaved settings forms can now stop in-app navigation
 * (sidebar, links), not only reloads. The nested <Routes> below are unchanged;
 * they run as descendant routes of the single catch-all data route.
 */
function AppRoutes() {
  return (
    <SessionBootstrap>
      <Suspense fallback={<LoadingScreen />}>
        <Routes>
          {/* Public routes */}
          <Route path={ROUTES.VERIFY_EMAIL} element={<VerifyEmail />} />
          {/* ARCH43-S2:public-request-route. No account: the single-use token is the credential. */}
          <Route path="/request/:token" element={<DocumentRequestUpload />} />
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

          {/*
            ARCH-30 Tranche 1 (T4-F4). Federated login completion.

            Outside PublicRoute AND outside PrivateRoute, deliberately.
            PrivateRoute redirects a browser with no persisted session to
            /login before the refresh cookie is ever exchanged, which is
            the loop this route exists to break. PublicRoute redirects a
            browser WITH a persisted session straight to the dashboard,
            skipping the exchange and keeping the previous user's profile
            in the store. Under AuthLayout so a tenant host shows its own
            branding while the exchange runs.
          */}
          <Route element={<AuthLayout />}>
            <Route
              path={ROUTES.SSO_COMPLETE}
              element={<SsoComplete />}
            />
          </Route>

          {/* Authenticated, tenant-independent */}
          <Route element={<PrivateRoute />}>
            <Route
              path={ROUTES.ONBOARDING}
              element={<CreateOrganizationPage />}
            />
            <Route
              path={ROUTES.NEW_ORGANIZATION}
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
                  index
                  element={
                    <Navigate to={ROUTE_PATTERNS.organizationSettings} replace />
                  }
                />
                <Route
                  path={ROUTE_PATTERNS.organizationSettings}
                  element={<OrganizationGeneral />}
                />
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
                {/* ARCH-35. The page resolves the organization and the
                    capability from the same hooks every gated page
                    uses, so the gate cannot be forgotten at a call site. */}
                <Route
                  path={ROUTE_PATTERNS.organizationAutonomy}
                  element={<AutonomySettings />}
                />
              </Route>
            </Route>

            <Route
              path={ROUTE_PATTERNS.platformShell}
              element={<SuperAdminGuard />}
            >
              {/*
                ARCH-29 Tranche 1. A pathless layout route, so the guard
                stays a guard and the chrome stays chrome. Every future
                platform page mounts inside PlatformLayout and inherits the
                cross-tenant scope band and the exit; a page added as a
                sibling of this element would ship without both.
              */}
              <Route element={<PlatformLayout />}>
                <Route
                  path={ROUTE_PATTERNS.platformMargins}
                  element={<AdminMarginsHub />}
                />
              </Route>
            </Route>

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
                  element={<ReviewHub />}
                />
                {/* ARCH-36. ARCH-33's queue, routed for the first time. */}
                <Route
                  path={ROUTE_PATTERNS.workspaceAssertions}
                  element={<AssertionReviewPage />}
                />
                {/* ARCH-31. The policies route is declared
                    BEFORE the :caseId route: react-router would
                    otherwise match "policies" as a case id and
                    render the comparison grid against a case
                    that does not exist. */}
                <Route
                  path={ROUTE_PATTERNS.workspaceRedaction}
                  element={<RedactionStudio />}
                />
                {/* ARCH-34. The page takes no props: it resolves the
                    workspace and the capability from the same two hooks
                    every other capability-gated page uses, so the gate
                    cannot be forgotten at a call site. */}
                <Route
                  path={ROUTE_PATTERNS.workspaceRadar}
                  element={<ForensicAuditRadar />}
                />
                <Route
                  path={ROUTE_PATTERNS.workspaceExtractionMemory}
                  element={<ExtractionMemory />}
                />
                <Route path={ROUTE_PATTERNS.workspaceEntities} element={<Entities />} />
                <Route path={ROUTE_PATTERNS.workspaceEntity} element={<Entity360 />} />
                {/* ARCH43-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceCases} element={<Cases />} />
                <Route path={ROUTE_PATTERNS.workspaceCase} element={<CaseDetailPage />} />
                <Route path={ROUTE_PATTERNS.workspacePacketSplits} element={<PacketSplits />} />
                <Route path={ROUTE_PATTERNS.workspacePacketSplit} element={<SplitReview />} />
                {/* ARCH44-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceTables} element={<Tables />} />
                <Route path={ROUTE_PATTERNS.workspaceTable} element={<TableViewer />} />
                {/* ARCH45-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceCorroborations} element={<Corroborations />} />
                <Route path={ROUTE_PATTERNS.workspaceCorroboration} element={<CorroborationRun />} />
                {/* ARCH46-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceObligations} element={<Obligations />} />
                <Route path={ROUTE_PATTERNS.workspaceObligation} element={<ObligationDetail />} />
                <Route
                  path={ROUTE_PATTERNS.workspaceProcurement}
                  element={<ProcurementCaseQueue />}
                />
                <Route
                  path={ROUTE_PATTERNS.workspaceProcurementPolicies}
                  element={<TolerancePolicyEditor />}
                />
                <Route
                  path={ROUTE_PATTERNS.workspaceProcurementCase}
                  element={<ThreeWayComparison />}
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
  );
}

const router = createBrowserRouter([{ path: "*", element: <AppRoutes /> }]);

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
      <UnsavedChangesDialogHost />

      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}
