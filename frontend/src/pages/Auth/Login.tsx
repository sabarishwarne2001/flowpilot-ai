import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useAuthStore } from "@/store/useAuthStore";
import { authApi } from "@/services/api/auth";
import { ROUTES } from "@/constants/routes";
import { API_ERROR_CODES } from "@/constants/errorCodes";
import { ApiError } from "@/services/api/client";
import { isSafeRedirectPath } from "@/routes/tenantPaths";
import { Mail, Lock, Loader2 } from "lucide-react";

export const Login: React.FC = () => {
  const navigate = useNavigate();
  const location = useLocation();

  /**
   * Where to land after a successful sign-in.
   *
   * ARCH-05 Step 0.5 companion. Every guard in the application builds
   * `/login?redirect=…` — loginPathWithRedirect exists for exactly that — and
   * this page ignored the parameter entirely, sending everyone to the
   * workspace picker. Session expiry on a deep link therefore lost the
   * destination, which is half of the defect ARCH-01 set out to fix.
   *
   * It became load-bearing at Step 0.5: an invitee arriving without a session
   * is the majority case for invitation acceptance, and without this they sign
   * in, land on the picker, and never return to the invitation they were
   * holding.
   *
   * Validated with isSafeRedirectPath rather than a local allowlist. That
   * function is the one tenantPaths self-checks, it rejects protocol-relative
   * and scheme-bearing values, and a second copy of open-redirect logic is a
   * second place for it to be wrong.
   */
  const redirectTo = (() => {
    const requested = new URLSearchParams(location.search).get("redirect");
    return requested && isSafeRedirectPath(requested)
      ? requested
      : ROUTES.WORKSPACES;
  })();

  // Local component states
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  // Auth store actions
  const setToken = useAuthStore((state) => state.setToken);
  const setAuth = useAuthStore((state) => state.setAuth);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email || !password || isLoading) return;

    setIsLoading(true);
    setError(null);

    try {
      // 1. Authenticate and receive the JWT token
      const tokenResponse = await authApi.loginRequest({ email, password });

      // 2. Set the token locally so the /auth/me request can authenticate
      setToken(tokenResponse.access_token);

      // 3. Resolve the current user profile
      const userResponse = await authApi.getMeRequest();

      // 4. Commit the authenticated session
      setAuth(userResponse, tokenResponse.access_token);

      // 5. Shift viewport to the requested destination, or the picker.
      //    `replace` so the back button does not return to a login form the
      //    user has already satisfied.
      navigate(redirectTo, { replace: true });
    } catch (err) {
      if (err instanceof ApiError) {
        if (err.code === API_ERROR_CODES.UNAUTHORIZED) {
          setError("Incorrect email or password.");
        } else {
          setError(err.message);
        }
      } else {
        setError("An unexpected error occurred. Please try again.");
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[350px]">
      <div className="flex flex-col space-y-2 text-center select-none">
        <h1 className="text-2xl font-semibold tracking-tight">
          Welcome back
        </h1>
        <p className="text-sm text-muted-foreground">
          Enter your email to sign in to your account
        </p>
      </div>

      <form onSubmit={handleSubmit} className="space-y-4">
        {error && (
          <div className="rounded-md bg-destructive/15 p-3 text-sm text-destructive" role="alert">
            {error}
          </div>
        )}

        <div className="space-y-2">
          <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70" htmlFor="email">
            Email
          </label>
          <div className="relative">
            <Mail className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              id="email"
              placeholder="name@example.com"
              type="email"
              autoCapitalize="none"
              autoComplete="email"
              autoCorrect="off"
              disabled={isLoading}
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 pl-10 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              required
            />
          </div>
        </div>

        <div className="space-y-2">
          <label className="text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70" htmlFor="password">
            Password
          </label>
          <div className="relative">
            <Lock className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
            <input
              id="password"
              placeholder="••••••••"
              type="password"
              autoComplete="current-password"
              disabled={isLoading}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              className="flex h-10 w-full rounded-md border border-input bg-background px-3 py-2 pl-10 text-sm ring-offset-background file:border-0 file:bg-transparent file:text-sm file:font-medium placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:cursor-not-allowed disabled:opacity-50"
              required
            />
          </div>
        </div>

        <button
          type="submit"
          disabled={isLoading}
          className="inline-flex items-center justify-center rounded-md text-sm font-medium ring-offset-background transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 disabled:pointer-events-none disabled:opacity-50 bg-primary text-primary-foreground hover:bg-primary/90 h-10 px-4 py-2 w-full"
        >
          {isLoading ? (
            <>
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
              Signing in...
            </>
          ) : (
            "Continue"
          )}
        </button>
        <div className="flex justify-end">
          <Link
            to={ROUTES.FORGOT_PASSWORD}
            className="text-sm text-muted-foreground underline-offset-4 hover:underline"
          >
            Forgot your password?
          </Link>
        </div>
      </form>

      <footer className="pt-2 text-center select-none">
        <p className="text-sm text-muted-foreground">
          Don't have an account?{" "}
          <Link
            to={ROUTES.REGISTER}
            className="underline underline-offset-4 hover:text-primary transition-colors"
          >
            Sign up
          </Link>
        </p>
      </footer>
    </div>
  );
};

export default Login;
