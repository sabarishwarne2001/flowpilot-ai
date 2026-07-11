import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { Eye, EyeOff, Loader2 } from "lucide-react";

import { registerSchema, type RegisterInput } from "@/utils/validation";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";

/**
 * Registration page for FlowPilot AI.
 *
 * Validates user input with React Hook Form + Zod,
 * registers a new account, then redirects users
 * to the login screen.
 */
export const Register: React.FC = () => {
  const navigate = useNavigate();

  const [showPassword, setShowPassword] = useState(false);

  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors, isSubmitting },
  } = useForm<RegisterInput>({
    resolver: zodResolver(registerSchema),
    shouldFocusError: true,
    defaultValues: {
      email: "",
      password: "",
      confirmPassword: "",
    },
  });

  const togglePassword = () => setShowPassword((value) => !value);

  const toggleConfirmPassword = () => setShowConfirmPassword((value) => !value);
  const onSubmit = async (data: RegisterInput): Promise<void> => {
    const payload = {
      email: data.email.trim(),
      password: data.password,
    };

    const registerPromise = authApi.registerRequest(payload);

    try {
      await toast.promise(registerPromise, {
        loading: "Creating your FlowPilot account...",

        success: () => {
          navigate(ROUTES.LOGIN, {
            replace: true,
          });

          return "Account created successfully! Please sign in.";
        },

        error: (error: unknown) => {
          if (error instanceof ApiError) {
            return error.message ?? "Registration failed. Please try again.";
          }

          return "An unexpected registration error occurred.";
        },
      });
    } finally {
      // Reserved for future cleanup logic.
    }
  };

  return (
    <div className="space-y-6">
      {/* ===========================
          Header
      =========================== */}

      <div className="select-none space-y-2">
        <h1 className="text-2xl font-extrabold tracking-tight">
          Create an Account
        </h1>

        <p className="text-sm font-semibold leading-relaxed text-muted-foreground">
          Sign up to begin automating your business documents.
        </p>
      </div>
      {/* ===========================
          Registration Form
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

        {/* Password */}
        <div className="space-y-1.5">
          <label
            htmlFor="password"
            className="select-none text-xs font-bold uppercase tracking-wider text-muted-foreground"
          >
            Password
          </label>

          <div className="relative">
            <input
              {...register("password")}
              id="password"
              type={showPassword ? "text" : "password"}
              placeholder="••••••••"
              autoComplete="new-password"
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
              onClick={togglePassword}
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

        {/* Confirm Password */}
        <div className="space-y-1.5">
          <label
            htmlFor="confirmPassword"
            className="select-none text-xs font-bold uppercase tracking-wider text-muted-foreground"
          >
            Confirm Password
          </label>

          <div className="relative">
            <input
              {...register("confirmPassword")}
              id="confirmPassword"
              type={showConfirmPassword ? "text" : "password"}
              placeholder="••••••••"
              autoComplete="new-password"
              disabled={isSubmitting}
              aria-invalid={!!errors.confirmPassword}
              aria-describedby={
                errors.confirmPassword ? "confirmPassword-error" : undefined
              }
              className={`w-full rounded-lg border bg-background py-2.5 pl-3.5 pr-11 text-sm font-semibold transition-all focus:outline-none focus:ring-2 focus:ring-primary/20 ${
                errors.confirmPassword
                  ? "border-destructive focus:border-destructive"
                  : "border-border hover:border-muted-foreground/30 focus:border-primary"
              }`}
            />
            <button
              type="button"
              onClick={toggleConfirmPassword}
              disabled={isSubmitting}
              tabIndex={-1}
              aria-label={
                showConfirmPassword
                  ? "Hide Confirm Password"
                  : "Show Confirm Password"
              }
              className="absolute right-3.5 top-1/2 -translate-y-1/2 text-muted-foreground/80 transition-colors hover:text-foreground disabled:opacity-50"
            >
              {showConfirmPassword ? (
                <EyeOff className="h-4 w-4" />
              ) : (
                <Eye className="h-4 w-4" />
              )}
            </button>
          </div>

          {errors.confirmPassword && (
            <p
              id="confirmPassword-error"
              role="alert"
              className="pt-0.5 text-xs font-semibold text-destructive"
            >
              {errors.confirmPassword.message}
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
              Creating account...
            </>
          ) : (
            "Create Account"
          )}
        </button>
      </form>
      {/* ===========================
          Footer
      =========================== */}

      <footer className="pt-2 text-center select-none">
        <p className="text-sm font-medium leading-none text-muted-foreground">
          Already have an account?{" "}
          <Link
            to={ROUTES.LOGIN}
            className="font-bold text-primary hover:underline"
          >
            Sign in instead
          </Link>
        </p>
      </footer>
    </div>
  );
};

export default Register;
