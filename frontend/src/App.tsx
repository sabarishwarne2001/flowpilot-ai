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
import TenantGuard from "@/routes/TenantGuard";

import { ROUTE_PATTERNS } from "@/routes/tenantPaths";
import { ROUTES } from "@/constants/routes";

/**
 * Application route tree for FlowPilot AI.
 *
 * ARCH-01 replaced the inline OnboardingGuard that previously lived here. It
 * called getWorkspace() and treated any falsy result as "no workspace", so an
 * expired token and a genuinely membership-less user produced the same signal
 * — session expiry sent people to the workspace creation screen instead of the
 * login page, and removed members founded phantom organizations.
 *
 * Responsibility is now split across three guards, each with one job:
 *
 *   PrivateRoute   validates the session against /me/context before rendering
 *                  anything. It no longer trusts the rehydrated localStorage
 *                  flag, which said only "this browser once held a session".
 *
 *   TenantGuard    resolves organization and workspace, reconciles them
 *                  against the URL, and publishes the result to descendants.
 *                  Handles all six tenant states explicitly.
 *
 *   LegacyRouteRedirect  forwards pre-ARCH-01 flat paths to their
 *                  tenant-scoped equivalents.
 *
 * ROUTE ORDERING
 *
 * Static segments outrank dynamic ones in React Router v6, so /work-items
 * matches before /:orgSlug/:workspaceSlug. That ranking is a convenience; the
 * real guarantee is the backend's reserved-slug list, which makes it
 * impossible to name an organization "work-items" in the first place.
 */
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
        <Suspense fallback={<LoadingScreen />}>
          <Routes>

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
      </BrowserRouter>
    </ErrorBoundary>
  );
}
