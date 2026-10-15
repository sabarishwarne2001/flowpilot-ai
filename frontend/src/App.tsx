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

import { BrandTheme } from "@/components/branding/BrandTheme";
import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { LoadingScreen } from "@/components/common/LoadingScreen";
import OrganizationPolicyNotice from "@/components/auth/OrganizationPolicyNotice";
import StepUpReauthModal from "@/components/auth/StepUpReauthModal";

import { AuthLayout } from "@/layouts/AuthLayout";

// §B.1 (session 4): pages are lazy (only the small 404 page is not). A page imported statically
// here is downloaded by every visitor before the first screen renders, with its libraries: the
// assistant canvas alone put pdf.js (~125 KB gzipped) on the login screen.
const Assistant = lazy(() =>
  import("@/pages/Assistant/Assistant").then((m) => ({ default: m.Assistant })),
);
const AssistantCanvas = lazy(() => import("@/pages/Assistant/AssistantCanvas"));

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
// ARCH50-S2:lazy-pages
const SovereignConsole = lazy(() => import("@/pages/admin/SovereignConsole"));
const RevOpsConsole = lazy(() => import("@/pages/admin/RevOpsConsole"));
const JobsConsole = lazy(() => import("@/pages/admin/JobsConsole"));
const OrganizationEgress = lazy(() => import("@/pages/organization/OrganizationEgress"));
const PlatformLayout = lazy(() => import("@/layouts/PlatformLayout"));
// §B.1 (session 4): lazy like PlatformLayout. Their sidebars, menus and banners (with the
// floating-ui positioning library) are ~50 KB gzipped that a signed-out visitor never sees.
// Their guards start the download while the tenant resolves.
const DashboardLayout = lazy(() => import("@/layouts/DashboardLayout"));
const OrganizationLayout = lazy(() => import("@/layouts/OrganizationLayout"));
// HELM:routes. Helm, the flagship engine: a mode with its own layout (brief §10.1).
const HelmLayout = lazy(() => import("@/layouts/HelmLayout"));
const HelmCockpit = lazy(() => import("@/pages/helm/HelmCockpit"));
const HelmPlan = lazy(() => import("@/pages/helm/HelmPlan"));
const HelmMandates = lazy(() => import("@/pages/helm/HelmMandates"));
const HelmLedger = lazy(() => import("@/pages/helm/HelmLedger"));
const HelmTimeMachine = lazy(() => import("@/pages/helm/HelmTimeMachine"));
const HelmCertificates = lazy(() => import("@/pages/helm/HelmCertificates"));
const HelmCertificate = lazy(() => import("@/pages/helm/HelmCertificates").then((m) => ({ default: m.HelmCertificate })));
const HelmSettings = lazy(() => import("@/pages/helm/HelmSettings"));
const HelmStart = lazy(() => import("@/pages/helm/HelmStart"));
const HelmFirstRun = lazy(() => import("@/pages/helm/HelmFirstRun"));
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
// Campaign session 4: the public pricing and legal pages.
const Pricing = lazy(() => import("@/pages/public/Pricing"));
const Terms = lazy(() => import("@/pages/public/Terms"));
const Privacy = lazy(() => import("@/pages/public/Privacy"));
// ARCH44-S2:lazy-pages
const Tables = lazy(() => import("@/pages/tables/Tables"));
const TableViewer = lazy(() => import("@/pages/tables/TableViewer"));
const Batches = lazy(() => import("@/pages/batches/Batches"));
const TruthMesh = lazy(() => import("@/pages/truthmesh/TruthMesh"));
const BatchDetail = lazy(() => import("@/pages/batches/BatchDetail"));
// ARCH45-S2:lazy-pages
const Corroborations = lazy(() => import("@/pages/corroboration/Corroborations"));
const CorroborationRun = lazy(() => import("@/pages/corroboration/CorroborationRun"));
// ARCH46-S2:lazy-pages
const Obligations = lazy(() => import("@/pages/obligations/Obligations"));
const ObligationDetail = lazy(() => import("@/pages/obligations/ObligationDetail"));
// ARCH47-S2:lazy-pages
const ErpPosting = lazy(() => import("@/pages/erp/ErpPosting"));
const ErpPostingDetail = lazy(() => import("@/pages/erp/ErpPostingDetail"));
const ErpTargetDetail = lazy(() => import("@/pages/erp/ErpTargetDetail"));
// ARCH49-S2:lazy-process
const ProcessIntelligence = lazy(() => import("@/pages/process/ProcessIntelligence"));
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

const Login = lazy(() => import("@/pages/Auth/Login"));
const SsoComplete = lazy(() => import("@/pages/Auth/SsoComplete"));
const Register = lazy(() => import("@/pages/Auth/Register"));
const VerifyEmail = lazy(() => import("@/pages/Auth/VerifyEmail"));
const ForgotPassword = lazy(() => import("@/pages/Auth/ForgotPassword"));
const ResetPassword = lazy(() => import("@/pages/Auth/ResetPassword"));
const InvitationAcceptPage = lazy(() => import("@/pages/Auth/InvitationAcceptPage"));
const ConfirmEmailChange = lazy(() => import("@/pages/Auth/ConfirmEmailChange"));
const Automation = lazy(() => import("@/pages/Automation/Automation"));
const Dashboard = lazy(() => import("@/pages/Dashboard/Dashboard"));
const Notifications = lazy(() => import("@/pages/Notifications/Notifications"));
const WorkItemDetails = lazy(() => import("@/pages/WorkItems/WorkItemDetails"));
const WorkItems = lazy(() => import("@/pages/WorkItems/WorkItems"));
const Settings = lazy(() => import("@/pages/Settings/Settings"));
const CreateOrganizationPage = lazy(() => import("@/pages/Tenant/CreateOrganizationPage"));
const NoAccess = lazy(() => import("@/pages/Tenant/NoAccess"));
const WorkspacePicker = lazy(() => import("@/pages/Tenant/WorkspacePicker"));
const CreateWorkspacePage = lazy(() => import("@/pages/Tenant/CreateWorkspacePage"));

import { NotFound } from "@/pages/NotFound";

import LegacyRouteRedirect from "@/routes/LegacyRouteRedirect";
import { PrivateRoute } from "@/routes/PrivateRoute";
import { PublicRoute } from "@/routes/PublicRoute";
import { SessionBootstrap } from "@/routes/SessionBootstrap";
import OrganizationGuard from "@/routes/OrganizationGuard";
import HelmGuard from "@/routes/HelmGuard";
// F-008 / F-053 / F-054: role screens instead of 403 error pages.
import { BILLING_READERS, OWNERS_AND_ADMINS, RequireOrganizationRole } from "@/routes/RequireOrganizationRole";
import { RequireWorkspaceRole } from "@/routes/RequireWorkspaceRole";
import SuperAdminGuard from "@/routes/SuperAdminGuard";
import TenantGuard from "@/routes/TenantGuard";

import { ROUTE_PATTERNS } from "@/routes/tenantPaths";
import { ROUTES } from "@/constants/routes";

/** The flat addresses from before ARCH-01, forwarded into the person's workspace. */
const LEGACY_PREFIXES = ["work-items", "assistant", "automation", "notifications", "settings", "profile", "account"] as const;

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
          {/* Campaign session 4 (CAMPAIGN-MAP 4.3). Outside PublicRoute and PrivateRoute:
              readable signed out and signed in. */}
          <Route path={ROUTES.PRICING} element={<Pricing />} />
          <Route path={ROUTES.TERMS} element={<Terms />} />
          <Route path={ROUTES.PRIVACY} element={<Privacy />} />
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
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationApiKeys /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationWebhooks}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationWebhooks /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationEmail}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationEmailSettings /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationNotifications}
                  element={<OrganizationNotifications />}
                />
                {/* F-194: a MEMBER opening Billing by its address got the page and its 403s. */}
                <Route
                  path={ROUTE_PATTERNS.organizationBilling}
                  element={<RequireOrganizationRole allowed={BILLING_READERS}><BillingHub /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationBillingReturn}
                  element={<RequireOrganizationRole allowed={BILLING_READERS}><CheckoutReturn /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationIdentity}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><IdentityAdminHub /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationAudit}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><AuditExplorer asPage /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationSLOs}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationSLOs /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationCompliance}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationCompliance /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationDeveloper}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationDeveloperPortal /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationBYOK}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationBYOK /></RequireOrganizationRole>}
                />
                {/* ARCH50-S2:route-egress */}
                <Route
                  path={ROUTE_PATTERNS.organizationEgress}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationEgress /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationAnalytics}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationAnalytics /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationBranding}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><OrganizationBranding /></RequireOrganizationRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.organizationMarketplace}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><MarketplaceCatalog /></RequireOrganizationRole>}
                />
                {/* ARCH-35. The page resolves the organization and the
                    capability from the same hooks every gated page
                    uses, so the gate cannot be forgotten at a call site. */}
                <Route
                  path={ROUTE_PATTERNS.organizationAutonomy}
                  element={<RequireOrganizationRole allowed={OWNERS_AND_ADMINS}><AutonomySettings /></RequireOrganizationRole>}
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
                {/* F-009: /admin had no index route and rendered an empty
                    body; it opens the first platform console. */}
                <Route
                  index
                  element={<Navigate to={ROUTE_PATTERNS.platformMargins} replace />}
                />
                <Route
                  path={ROUTE_PATTERNS.platformMargins}
                  element={<AdminMarginsHub />}
                />
                {/* ARCH50-S2:route-platform */}
                <Route
                  path={ROUTE_PATTERNS.platformSovereign}
                  element={<SovereignConsole />}
                />
                <Route
                  path={ROUTE_PATTERNS.platformRevops}
                  element={<RevOpsConsole />}
                />
                <Route
                  path={ROUTE_PATTERNS.platformJobs}
                  element={<JobsConsole />}
                />
              </Route>
            </Route>

            <Route
              path={ROUTE_PATTERNS.partnerPortalShell}
              element={<PartnerPortal />}
            />

            {/* Legacy redirects. Campaign session 4 (F-364): each prefix also has an explicit
                two-segment route. "/work-items/*" and the workspace shell's dashboard
                ("/:orgSlug/:workspaceSlug", index) rank equal for "/work-items/<id>", and the
                shell won: the old address was read as organization "work-items" and sent to the
                workspace picker. A named second segment outranks both. */}
            <Route path="/" element={<LegacyRouteRedirect />} />
            {LEGACY_PREFIXES.flatMap((prefix) => [
              <Route key={`${prefix}/*`} path={`/${prefix}/*`} element={<LegacyRouteRedirect />} />,
              <Route key={`${prefix}/:legacySub`} path={`/${prefix}/:legacySub`} element={<LegacyRouteRedirect />} />,
            ])}

            {/* HELM:route-shell. `/:orgSlug/helm/…` (brief §10.1). After the legacy redirects, so
                `/work-items/helm` stays a legacy address (equal rank: the earlier route wins); it
                outranks the workspace shell below because "helm" is a static segment.
                OrganizationGuard resolves the organization from the address; HelmGuard admits
                owners, admins and the billing role. */}
            <Route path={ROUTE_PATTERNS.helmShell} element={<OrganizationGuard />}>
              <Route element={<HelmGuard />}>
                <Route element={<HelmLayout />}>
                  <Route index element={<HelmCockpit />} />
                  <Route path={ROUTE_PATTERNS.helmPlan} element={<HelmPlan />} />
                  <Route path={ROUTE_PATTERNS.helmMandates} element={<HelmMandates />} />
                  <Route path={ROUTE_PATTERNS.helmLedger} element={<HelmLedger />} />
                  <Route path={ROUTE_PATTERNS.helmTimeMachine} element={<HelmTimeMachine />} />
                  <Route path={ROUTE_PATTERNS.helmCertificates} element={<HelmCertificates />} />
                  <Route path={ROUTE_PATTERNS.helmCertificate} element={<HelmCertificate />} />
                  <Route path={ROUTE_PATTERNS.helmSettings} element={<HelmSettings />} />
                  <Route path={ROUTE_PATTERNS.helmStart} element={<HelmStart />} />
                  <Route path={ROUTE_PATTERNS.helmSetup} element={<HelmFirstRun />} />
                  <Route path="*" element={<NotFound />} />
                </Route>
              </Route>
            </Route>

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
                  element={<RequireWorkspaceRole minimum="CONTRIBUTOR"><Automation /></RequireWorkspaceRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.workspaceAutomationTimeline}
                  element={<RequireWorkspaceRole minimum="CONTRIBUTOR"><ExecutionTimeline /></RequireWorkspaceRole>}
                />
                <Route
                  path={ROUTE_PATTERNS.workspaceVerification}
                  element={<RequireWorkspaceRole minimum="CONTRIBUTOR"><ReviewHub /></RequireWorkspaceRole>}
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
                <Route path={ROUTE_PATTERNS.workspaceTruthMesh} element={<TruthMesh />} />
                <Route path={ROUTE_PATTERNS.workspaceBatches} element={<Batches />} />
                <Route path={ROUTE_PATTERNS.workspaceBatch} element={<BatchDetail />} />
                {/* ARCH45-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceCorroborations} element={<Corroborations />} />
                <Route path={ROUTE_PATTERNS.workspaceCorroboration} element={<CorroborationRun />} />
                {/* ARCH46-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceObligations} element={<Obligations />} />
                <Route path={ROUTE_PATTERNS.workspaceObligation} element={<ObligationDetail />} />
                {/* ARCH47-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceErp} element={<ErpPosting />} />
                <Route path={ROUTE_PATTERNS.workspaceErpPosting} element={<ErpPostingDetail />} />
                <Route path={ROUTE_PATTERNS.workspaceErpTarget} element={<ErpTargetDetail />} />
                {/* ARCH49-S2:routes */}
                <Route path={ROUTE_PATTERNS.workspaceProcess} element={<ProcessIntelligence />} />
                <Route path={ROUTE_PATTERNS.workspaceProcessProposal} element={<ProcessIntelligence />} />
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
      <BrandTheme />
      <StepUpReauthModal />
      <OrganizationPolicyNotice />
      <UnsavedChangesDialogHost />

      <RouterProvider router={router} />
    </ErrorBoundary>
  );
}
