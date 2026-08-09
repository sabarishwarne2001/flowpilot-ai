import { Suspense } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Toaster } from "sonner";

import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { LoadingScreen } from "@/components/common/LoadingScreen";

import { AuthLayout } from "@/layouts/AuthLayout";
import { DashboardLayout } from "@/layouts/DashboardLayout";

import { Assistant } from "@/pages/Assistant/Assistant";
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
import TenantGuard from "@/routes/TenantGuard";

import { ROUTE_PATTERNS } from "@/routes/tenantPaths";
import { ROUTES } from "@/constants/routes";

export default function App() {
  return (
    <ErrorBoundary>
      <Toaster
        position="top-right"
        richColors
        closeButton
        duration={4000}
      />

      <BrowserRouter>
        {/* One /auth/refresh before anything renders. The access token no
            longer survives a reload (ARCH-03 Step 7), so without this every
            reload of a live session flashes the login screen while the 401
            interceptor catches up. */}
        <SessionBootstrap>
          <Suspense fallback={<LoadingScreen />}>
            <Routes>

              {/* ======================================
                  Email Verification (PUBLIC)

                  The token arrives in the URL fragment, so this must be
                  reachable without a session — the link opens from a mail
                  client, usually in a signed-out browser (ARCH-03 §B.9).
              ======================================= */}
              <Route path={ROUTES.VERIFY_EMAIL} element={<VerifyEmail />} />

              {/* ======================================
                  Password recovery (PUBLIC)

                  RESET_PASSWORD reads its token from the URL fragment, so it
                  must be reachable without a session (ARCH-03 §B.9).
              ======================================= */}
              <Route
                path={ROUTES.FORGOT_PASSWORD}
                element={<ForgotPassword />}
              />
              <Route
                path={ROUTES.RESET_PASSWORD}
                element={<ResetPassword />}
              />

              {/* ======================================
                  Invitation Acceptance (PUBLIC)

                  Preview is public so a recipient can see who invited them
                  before creating an account. Accepting requires a session — the
                  page handles that transition itself.
              ======================================= */}
              <Route
                path={ROUTES.INVITATION_ACCEPT}
                element={<InvitationAcceptPage />}
              />

              {/* ======================================
                  Public Routes
              ======================================= */}
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

              {/* ======================================
                  Authenticated, tenant-independent

                  These require a session but no tenant, so they mount under
                  PrivateRoute alone. Placing them under TenantGuard would be a
                  redirect loop: the guard sends a membership-less actor to
                  /onboarding, which would then need a tenant to render.
              ======================================= */}
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

                {/* Workspace creation. Organization-scoped, so it mounts under
                    PrivateRoute rather than TenantGuard — the actor may have no
                    workspace yet, which is exactly when they need this. */}
                <Route
                  path={ROUTE_PATTERNS.organizationNewWorkspace}
                  element={<CreateWorkspacePage />}
                />

                {/* ======================================
                    Legacy flat paths

                    Forwarded to their tenant-scoped equivalents. Sidebar,
                    navigation.ts, and DashboardLayout still link here until
                    Step 8; these redirects keep every link working and remain
                    useful afterwards for bookmarks saved before ARCH-01.
                ======================================= */}
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

                {/* ======================================
                    Workspace-scoped routes

                    /:orgSlug/:workspaceSlug/... — the tenant is in the URL, so
                    deep links survive, two tabs can hold two workspaces, and a
                    refresh is idempotent.
                ======================================= */}
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
                      path={ROUTE_PATTERNS.workspaceAutomation}
                      element={<Automation />}
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

              {/* ======================================
                  404
              ======================================= */}
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
