import { Suspense } from "react";
import { BrowserRouter, Route, Routes, Navigate, Outlet } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Toaster } from "sonner";

import { ErrorBoundary } from "@/components/common/ErrorBoundary";
import { LoadingScreen } from "@/components/common/LoadingScreen";

import { AuthLayout } from "@/layouts/AuthLayout";
import { DashboardLayout } from "@/layouts/DashboardLayout";

import { Assistant } from "@/pages/Assistant/Assistant";
import { Login } from "@/pages/Auth/Login";
import { Register } from "@/pages/Auth/Register";
import { OnboardingPage } from "@/pages/Auth/OnboardingPage";
import InvitationAcceptPage from "@/pages/Auth/InvitationAcceptPage";
import { Automation } from "@/pages/Automation/Automation";
import { Dashboard } from "@/pages/Dashboard/Dashboard";
import { NotFound } from "@/pages/NotFound";
import { Notifications } from "@/pages/Notifications/Notifications";
import Settings from "@/pages/Settings/Settings";
import { WorkItemDetails } from "@/pages/WorkItems/WorkItemDetails";
import { WorkItems } from "@/pages/WorkItems/WorkItems";

import { PrivateRoute } from "@/routes/PrivateRoute";
import { PublicRoute } from "@/routes/PublicRoute";

import { getWorkspace } from "@/services/api/workspace";
import { useAuthStore } from "@/store/useAuthStore";
import { ROUTES } from "@/constants/routes";

// ============================================================================
// Onboarding Redirect Guard (Sprint 2)
// ============================================================================

const OnboardingGuard: React.FC = () => {
  const token = useAuthStore((state) => state.token);

  const { data: workspace, isLoading } = useQuery({
    queryKey: ["workspace", token],
    queryFn: getWorkspace,
    retry: false,
    enabled: !!token,
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-background flex items-center justify-center text-foreground">
        <span className="text-sm font-semibold animate-pulse text-muted-foreground">
          Resolving workspace routing...
        </span>
      </div>
    );
  }

  // If authenticated user does not belong to any workspace, force redirect to onboarding
  if (!workspace) {
    return <Navigate to="/onboarding" replace />;
  }

  return <Outlet />;
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

      <BrowserRouter>
        <Suspense fallback={<LoadingScreen />}>
          <Routes>

            {/* ======================================
                Invitation Acceptance (PUBLIC)
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
                Protected Routes
            ======================================= */}
            <Route element={<PrivateRoute />}>
              {/* Workspace Onboarding Page */}
              <Route path="/onboarding" element={<OnboardingPage />} />

              {/* Secure Workspace Context Routes */}
              <Route element={<OnboardingGuard />}>
                <Route element={<DashboardLayout />}>
                  <Route
                    path={ROUTES.DASHBOARD}
                    element={<Dashboard />}
                  />

                  <Route
                    path={ROUTES.WORK_ITEMS}
                    element={<WorkItems />}
                  />

                  <Route
                    path={ROUTES.WORK_ITEM_DETAILS}
                    element={<WorkItemDetails />}
                  />

                  <Route
                    path={ROUTES.ASSISTANT}
                    element={<Assistant />}
                  />

                  <Route
                    path={ROUTES.AUTOMATION}
                    element={<Automation />}
                  />

                  <Route
                    path={ROUTES.NOTIFICATIONS}
                    element={<Notifications />}
                  />

                  <Route
                    path={ROUTES.SETTINGS}
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
