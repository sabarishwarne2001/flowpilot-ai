import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Eye, EyeOff, Loader2 } from "lucide-react";

import { loginSchema, type LoginInput } from "@/utils/validation";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";

import { useAuthStore } from "@/store/useAuthStore";
import { ROUTES } from "@/constants/routes";

/**
 * Login page for FlowPilot AI.
 *
 * Performs client-side validation using
 * React Hook Form + Zod and authenticates
 * users against the FastAPI backend.
 */
export const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const { setAuth, setToken, clearAuth } = useAuthStore();

  const [showPassword, setShowPassword] = useState(false);

  const redirectPath =
    (
      location.state as {
        from?: { pathname: string };
      } | null
    )?.from?.pathname ?? ROUTES.DASHBOARD;

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<LoginInput>({
    resolver: zodResolver(loginSchema),
    shouldFocusError: true,
    defaultValues: {
      email: "",
      password: "",
    },
  });

  const togglePasswordVisibility = () => {
    setShowPassword((value) => !value);
  };
  const onSubmit = async (data: LoginInput): Promise<void> => {
    const credentials: LoginInput = {
      ...data,
      email: data.email.trim(),
    };

    const loginPromise = (async () => {
      // Step 1: Authenticate and receive JWT
      const tokenResponse = await authApi.loginRequest(credentials);

      // Step 2: Temporarily cache token so /auth/me
      // can be authenticated by the Axios interceptor.
      setToken(tokenResponse.access_token);

      // Step 3: Fetch authenticated user profile.
      const userResponse = await authApi.getMeRequest();

      // Step 4: Persist full authenticated session.
      setAuth(userResponse, tokenResponse.access_token);

      // Step 5: Redirect to original destination.
      navigate(redirectPath, {
        replace: true,
      });

      return userResponse;
    })();

    try {
      await toast.promise(loginPromise, {
        loading: "Signing in...",
        success: "Welcome back to FlowPilot AI!",
        error: (error) =>
          error instanceof ApiError ? error.message : "Unable to sign in.",
      });
    } catch {
      clearAuth();
    } finally {
      // Reserved for future cleanup if temporary
      // authentication state is introduced.
    }
  };
  return (
    <div className="space-y-6">
      {/* ===========================
          Header
      =========================== */}

      <div className="select-none space-y-2">
        <h1 className="text-2xl font-extrabold tracking-tight">Sign In</h1>

        <p className="text-sm font-semibold leading-relaxed text-muted-foreground">
          Welcome back. Enter your credentials to access your document pipeline.
        </p>
      </div>

      {/* ===========================
          Login Form
      =========================== */}

      <form noValidate onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-1.5">
          <label
            htmlFor="email"
            className="select-none text-xs font-bold uppercase tracking-wider text-muted-foreground"
          >
            Email Address
          </label>

          <input
            {...register("email")}
            id="email"
            type="email"
            autoFocus
            autoComplete="email"
            placeholder="name@company.com"
            disabled={isSubmitting}
            aria-invalid={!!errors.email}
            aria-describedby={errors.email ? "email-error" : undefined}
            className={`w-full rounded-lg border bg-background px-3.5 py-2.5 text-sm font-semibold transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 ${
              errors.email
                ? "border-destructive focus:border-destructive"
                : "border-border hover:border-muted-foreground/30 focus:border-primary"
            }`}
          />

          {errors.email && (
            <p
              id="email-error"
              role="alert"
              className="pt-0.5 text-xs font-semibold text-destructive"
            >
              {errors.email.message}
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <div className="flex items-center justify-between select-none">
            <label
              htmlFor="password"
              className="text-xs font-bold uppercase tracking-wider text-muted-foreground"
            >
              Password
            </label>
          </div>

          <div className="relative">
            <input
              {...register("password")}
              id="password"
              type={showPassword ? "text" : "password"}
              autoComplete="current-password"
              placeholder="••••••••"
              disabled={isSubmitting}
              aria-invalid={!!errors.password}
              aria-describedby={errors.password ? "password-error" : undefined}
              className={`w-full rounded-lg border bg-background py-2.5 pl-3.5 pr-11 text-sm font-semibold transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 ${
                errors.password
                  ? "border-destructive focus:border-destructive"
                  : "border-border hover:border-muted-foreground/30 focus:border-primary"
              }`}
            />

            <button
              type="button"
              onClick={togglePasswordVisibility}
              disabled={isSubmitting}
              tabIndex={-1}
              aria-label={showPassword ? "Hide Password" : "Show Password"}
              className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/80 transition-colors hover:text-foreground disabled:opacity-50"
            >
              {showPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>

          {errors.password && (
            <p
              id="password-error"
              role="alert"
              className="pt-0.5 text-xs font-semibold text-destructive"
            >
              {errors.password.message}
            </p>
          )}
        </div>
        <button
          type="submit"
          disabled={isSubmitting}
          className="mt-2 flex w-full items-center justify-center rounded-lg bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground shadow-sm transition-all hover:bg-primary/95 active:scale-[0.98] disabled:pointer-events-none disabled:opacity-50"
        >
          {isSubmitting ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Signing in...
            </>
          ) : (
            "Continue"
          )}
        </button>
      </form>

      {/* ===========================
          Footer
      =========================== */}

      <footer className="pt-2 text-center select-none">
        <p className="text-sm font-medium leading-none text-muted-foreground">
          Don&apos;t have an account?{" "}
          <Link
            to={ROUTES.REGISTER}
            className="font-bold text-primary hover:underline"
          >
            Create one for free
          </Link>
        </p>
      </footer>
    </div>
  );
};

export default Login;
