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
const BillingHub = lazy(() => import("@/pages/billing/BillingHub"));
const CheckoutReturn = lazy(() => import("@/pages/billing/CheckoutReturn"));
const IdentityAdminHub = lazy(
  () => import("@/pages/identity/IdentityAdminHub"),
);
const AuditExplorer = lazy(() => import("@/pages/admin/AuditExplorer"));
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
                  </Route>
                </Route>

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
