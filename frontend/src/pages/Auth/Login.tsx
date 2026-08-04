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

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  const { setAuth, setToken, clearAuth } = useAuthStore();

  const [showPassword, setShowPassword] = useState(false);

  // Securely whitelist redirect destinations to mitigate Open Redirect vulnerabilities
  const isValidRedirect = (path: string): boolean => {
    if (!path) {
      return false;
    }
    // Enforce relative paths that prevent protocol switches (e.g. bypassing "https:")
    if (!path.startsWith("/") || path.startsWith("//")) {
      return false;
    }
    const cleanPath = path.split("?")[0]?.split("#")[0] || "";
    const allowedPrefixes = [
      "/",
      "/work-items",
      "/assistant",
      "/automation",
      "/notifications",
      "/profile",
      "/settings",
      "/account",
      "/invitations/accept"
    ];
    return allowedPrefixes.some(prefix => {
      if (prefix === "/") {
        return cleanPath === "/";
      }
      return cleanPath.startsWith(prefix);
    });
  };

  const searchParams = new URLSearchParams(location.search);
  const redirectParam = searchParams.get("redirect");
  const validatedRedirectParam = redirectParam && isValidRedirect(redirectParam) ? redirectParam : null;

  const fromLocation = (
    location.state as {
      from?: { pathname: string; search?: string };
    } | null
  )?.from;

  const validatedFromLocation = fromLocation && isValidRedirect(fromLocation.pathname) ? fromLocation : null;

  const redirectPath =
    validatedRedirectParam ||
    (validatedFromLocation
      ? `${validatedFromLocation.pathname}${validatedFromLocation.search ?? ""}`
      : ROUTES.DASHBOARD);

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
      const tokenResponse = await authApi.loginRequest(credentials);
      setToken(tokenResponse.access_token);
      const userResponse = await authApi.getMeRequest();
      setAuth(userResponse, tokenResponse.access_token);

      navigate(redirectPath, {
        replace: true,
      });

      return userResponse;
    })();

    try {
      await toast.promise(loginPromise, {
        loading: "Signing in...",
        success: "Welcome back!",
        error: (error) =>
          error instanceof ApiError ? error.message : "Unable to sign in.",
      });
    } catch {
      clearAuth();
    }
  };

  return (
    <div className="space-y-6">
      <div className="select-none space-y-2">
        <h1 className="text-2xl font-extrabold tracking-tight">Sign In</h1>
        <p className="text-sm font-semibold leading-relaxed text-muted-foreground">
          Welcome back. Enter your credentials to access your document pipeline.
        </p>
      </div>

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
            <p id="email-error" role="alert" className="pt-0.5 text-xs font-semibold text-destructive">
              {errors.email.message}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label
            htmlFor="password"
            className="text-xs font-bold uppercase tracking-wider text-muted-foreground"
          >
            Password
          </label>
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
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {errors.password && (
            <p id="password-error" role="alert" className="pt-0.5 text-xs font-semibold text-destructive">
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

      <footer className="pt-2 text-center select-none">
        <p className="text-sm font-medium leading-none text-muted-foreground">
          Don&apos;t have an account?{" "}
          <Link
            to={redirectParam ? `${ROUTES.REGISTER}?redirect=${encodeURIComponent(redirectParam)}` : ROUTES.REGISTER}
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
