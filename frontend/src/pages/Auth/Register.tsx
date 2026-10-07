import React, { useState } from "react";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { Link, useLocation } from "react-router-dom";
import { toast } from "sonner";
import { ArrowRight, Eye, EyeOff, Loader2, Lock, Mail, MailCheck } from "lucide-react";

import { registerSchema, type RegisterInput } from "@/utils/validation";
import PasswordStrengthMeter from "@/components/auth/PasswordStrengthMeter";

import { authApi } from "@/services/api/auth";
import { ApiError } from "@/services/api/client";
import { ROUTES } from "@/constants/routes";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import {
  AUTH_FIELD_ERROR,
  AUTH_INPUT,
  AUTH_INPUT_ICON,
  AUTH_LABEL,
  AUTH_LINK,
  AUTH_PRIMARY,
  AUTH_SUBTITLE,
  AUTH_TITLE,
  AUTH_TRAILING_BUTTON,
} from "@/components/auth/authStyles";

export const Register: React.FC = () => {
  const location = useLocation();

  const [showPassword, setShowPassword] = useState(false);
  const [showConfirmPassword, setShowConfirmPassword] = useState(false);

  /**
   * The redirect destination to resume after email verification (§B.8,
   * Option A: carry the validated redirect path through verification).
   *
   * ARCH-06 Step 9 replaced a local `isValidRedirect` helper here with the
   * shared `isSafeRedirectPath`.
   */
  const validatedRedirectParam = (() => {
    const requested = new URLSearchParams(location.search).get("redirect");
    return requested && isSafeRedirectPath(requested) ? requested : null;
  })();

  const {
    register,
    handleSubmit,
    watch,
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

  const [submitted, setSubmitted] = useState(false);
  const [submittedEmail, setSubmittedEmail] = useState("");

  const togglePassword = () => setShowPassword((value) => !value);
  const toggleConfirmPassword = () => setShowConfirmPassword((value) => !value);

  const onSubmit = async (data: RegisterInput): Promise<void> => {
    const payload = {
      email: data.email.trim(),
      password: data.password,
      ...(validatedRedirectParam ? { redirect: validatedRedirectParam } : {}),
    };

    setSubmittedEmail(payload.email);

    try {
      // F-118. sonner 2's toast.promise returns the toast's id, not the
      // request: awaiting it never fails, so a refused sign-up (a too-easy
      // password, the rate limit) still showed "Check your email".
      // unwrap() is the request itself, and rejects when it does.
      await toast
        .promise(authApi.registerRequest(payload), {
          loading: "Creating your FlowPilot account...",

          success: () => "Check your email to continue.",

          error: (error: unknown) => {
            if (error instanceof ApiError) {
              return error.message ?? "Registration failed. Please try again.";
            }

            return "An unexpected registration error occurred.";
          },
        })
        .unwrap();

      setSubmitted(true);
    } catch {
      // toast.promise has already surfaced it.
    }
  };

  if (submitted) {
    return (
      <div className="flex w-full flex-col items-center gap-4 py-4 text-center select-none animate-fade-in">
        <div className="flex h-12 w-12 items-center justify-center rounded-2xl border border-emerald-500/25 bg-emerald-500/10">
          <MailCheck className="h-6 w-6 text-emerald-500" aria-hidden="true" />
        </div>
        <h1 className={AUTH_TITLE}>Check your email</h1>
        <p className={`max-w-sm ${AUTH_SUBTITLE}`}>
          We have sent a message to <strong className="font-medium text-foreground">{submittedEmail}</strong>. Open the
          link inside to finish setting up your account.
        </p>
        <p className="max-w-sm text-xs leading-relaxed text-muted-foreground">
          Nothing arrived after a few minutes? Check your spam folder, and confirm the address above
          is spelled correctly.
        </p>
        <Link to={ROUTES.LOGIN} className={`text-sm ${AUTH_LINK}`}>
          Back to sign in
        </Link>
      </div>
    );
  }

  return (
    <div className="flex w-full flex-col">
      <div className="mb-6 space-y-1.5 select-none">
        <h1 className={AUTH_TITLE}>Create an Account</h1>
        <p className={AUTH_SUBTITLE}>Sign up to begin automating your business documents.</p>
      </div>

      <form noValidate onSubmit={handleSubmit(onSubmit)} className="space-y-4">
        <div className="space-y-1.5">
          <label htmlFor="email" className={`${AUTH_LABEL} select-none`}>
            Email Address
          </label>
          <div className="relative">
            <Mail className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              {...register("email")}
              id="email"
              type="email"
              autoComplete="email"
              placeholder="name@company.com"
              disabled={isSubmitting}
              aria-invalid={!!errors.email}
              aria-describedby={errors.email ? "email-error" : undefined}
              className={`${AUTH_INPUT} pl-9`}
            />
          </div>
          {errors.email && (
            <p id="email-error" role="alert" className={AUTH_FIELD_ERROR}>
              {errors.email.message}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="password" className={`${AUTH_LABEL} select-none`}>
            Password
          </label>
          <div className="relative">
            <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              {...register("password")}
              id="password"
              type={showPassword ? "text" : "password"}
              placeholder="••••••••"
              autoComplete="new-password"
              disabled={isSubmitting}
              aria-invalid={!!errors.password}
              aria-describedby={errors.password ? "password-error" : undefined}
              className={`${AUTH_INPUT} pl-9 pr-10`}
            />
            <button
              type="button"
              onClick={togglePassword}
              disabled={isSubmitting}
              tabIndex={-1}
              aria-label={showPassword ? "Hide Password" : "Show Password"}
              className={AUTH_TRAILING_BUTTON}
            >
              {showPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          <PasswordStrengthMeter password={watch("password")} userInputs={[watch("email")]} />
          {errors.password && (
            <p id="password-error" role="alert" className={AUTH_FIELD_ERROR}>
              {errors.password.message}
            </p>
          )}
        </div>

        <div className="space-y-1.5">
          <label htmlFor="confirmPassword" className={`${AUTH_LABEL} select-none`}>
            Confirm Password
          </label>
          <div className="relative">
            <Lock className={AUTH_INPUT_ICON} aria-hidden="true" />
            <input
              {...register("confirmPassword")}
              id="confirmPassword"
              type={showConfirmPassword ? "text" : "password"}
              placeholder="••••••••"
              autoComplete="new-password"
              disabled={isSubmitting}
              aria-invalid={!!errors.confirmPassword}
              aria-describedby={errors.confirmPassword ? "confirmPassword-error" : undefined}
              className={`${AUTH_INPUT} pl-9 pr-10`}
            />
            <button
              type="button"
              onClick={toggleConfirmPassword}
              disabled={isSubmitting}
              tabIndex={-1}
              aria-label={showConfirmPassword ? "Hide Confirm Password" : "Show Confirm Password"}
              className={AUTH_TRAILING_BUTTON}
            >
              {showConfirmPassword ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
            </button>
          </div>
          {errors.confirmPassword && (
            <p id="confirmPassword-error" role="alert" className={AUTH_FIELD_ERROR}>
              {errors.confirmPassword.message}
            </p>
          )}
        </div>

        <button type="submit" disabled={isSubmitting} className={`${AUTH_PRIMARY} mt-2`}>
          {isSubmitting ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              Creating account...
            </>
          ) : (
            <>
              Create Account
              <ArrowRight className="h-4 w-4" aria-hidden="true" />
            </>
          )}
        </button>
      </form>

      <p className="mt-6 border-t border-border/70 pt-5 text-center text-[13px] text-muted-foreground select-none">
        Already have an account?{" "}
        <Link
          to={
            validatedRedirectParam
              ? `${ROUTES.LOGIN}?redirect=${encodeURIComponent(validatedRedirectParam)}`
              : ROUTES.LOGIN
          }
          className={AUTH_LINK}
        >
          Sign in instead
        </Link>
      </p>
    </div>
  );
};

export default Register;
