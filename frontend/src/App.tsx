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
import { Automation } from "@/pages/Automation/Automation";
import { Dashboard } from "@/pages/Dashboard/Dashboard";
import { NotFound } from "@/pages/NotFound";
import { Notifications } from "@/pages/Notifications/Notifications";
import  Settings  from "@/pages/Settings/Settings";
import { WorkItemDetails } from "@/pages/WorkItems/WorkItemDetails";
import { WorkItems } from "@/pages/WorkItems/WorkItems";

import { PrivateRoute } from "@/routes/PrivateRoute";
import { PublicRoute } from "@/routes/PublicRoute";

import { ROUTES } from "@/constants/routes";

export default function App() {
  return (
    <ErrorBoundary>
      <Toaster position="top-right" richColors closeButton duration={4000} />

      <BrowserRouter>
        <Suspense fallback={<LoadingScreen />}>
          <Routes>
            {/* ===========================
                Public Routes
            =========================== */}
            <Route
              element={
                <PublicRoute>
                  <AuthLayout />
                </PublicRoute>
              }
            >
              <Route path={ROUTES.LOGIN} element={<Login />} />
              <Route path={ROUTES.REGISTER} element={<Register />} />
            </Route>

            {/* ===========================
                Protected Routes
            =========================== */}
            <Route
              element={
                <PrivateRoute>
                  <DashboardLayout />
                </PrivateRoute>
              }
            >
              <Route path={ROUTES.DASHBOARD} element={<Dashboard />} />
              <Route path={ROUTES.WORK_ITEMS} element={<WorkItems />} />
              <Route
                path={ROUTES.WORK_ITEM_DETAILS}
                element={<WorkItemDetails />}
              />
              <Route path={ROUTES.ASSISTANT} element={<Assistant />} />
              <Route path={ROUTES.AUTOMATION} element={<Automation />} />
              <Route path={ROUTES.NOTIFICATIONS} element={<Notifications />} />
              <Route path={ROUTES.SETTINGS} element={<Settings />} />
            </Route>

            {/* ===========================
                404
            =========================== */}
            <Route path={ROUTES.NOT_FOUND} element={<NotFound />} />
          </Routes>
        </Suspense>
      </BrowserRouter>
    </ErrorBoundary>
  );
}
