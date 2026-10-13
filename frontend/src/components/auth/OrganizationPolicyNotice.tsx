import React, { Suspense, lazy, useCallback, useEffect, useRef, useState } from "react";
import { KeyRound, Loader2, ShieldCheck, ShieldOff } from "lucide-react";
import { useQuery } from "@tanstack/react-query";

import { MFA_STATUS_KEY, getMfaStatus } from "@/services/api/mfa";
import { discoverSso, emailDomain, ssoStartHref } from "@/services/api/sso";
import { useAuthStore } from "@/store/useAuthStore";
import { useSessionGuardStore } from "@/store/useSessionGuardStore";
import { sanitizeRedirectPath } from "@/utils/security";

// §B.1 (session 4): lazy, because this notice is mounted on every screen and the panel brings the
// QR code library; it loads when an organization actually asks for two-factor sign-in.
const TwoFactorPanel = lazy(() => import("@/pages/Settings/TwoFactorPanel"));

/**
 * Campaign session 3. The organization's sign-in policy refused this session.
 *
 * "Require single sign-on" used to be shown as in force while password
 * sessions kept full access. The server now refuses every request into such an
 * organization with `ORGANIZATION_SSO_REQUIRED` until the person signs in
 * through the organization's identity provider. Without this notice each page
 * would fail on its own with a generic error; here the person is told once,
 * plainly, and offered the way back in: start single sign-on for their address
 * (they return to the page they were on), or go to another organization.
 * An organization that allows only approved networks refuses the same way
 * (`ORGANIZATION_NETWORK_NOT_ALLOWED`); signing in again does not help there,
 * so only the reason and the way out are shown. An organization that requires
 * two-factor sign-in (`ORGANIZATION_MFA_REQUIRED`) is answered here too: the
 * setup itself is in the notice (personal settings live inside a workspace,
 * which is exactly what is refused), and "Continue" appears once it is on.
 *
 * Mounted beside the step-up dialog, outside the router, so it navigates with
 * `window.location` like the SSO start does anyway.
 */
const OrganizationPolicyNotice: React.FC = () => {
  const refusal = useSessionGuardStore((state) => state.organizationPolicy);
  const clear = useSessionGuardStore((state) => state.clearOrganizationPolicy);
  const email = useAuthStore((state) => state.user?.email ?? "");
  const [starting, setStarting] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const primaryRef = useRef<HTMLButtonElement>(null);

  const open = refusal !== null;

  useEffect(() => {
    if (open) {
      setProblem(null);
      setStarting(false);
      primaryRef.current?.focus();
    }
  }, [open]);

  const startSso = useCallback(async () => {
    const domain = emailDomain(email);
    if (!domain) {
      setProblem("Your account has no work address to start single sign-on with.");
      return;
    }
    setStarting(true);
    setProblem(null);
    try {
      const discovery = await discoverSso(domain);
      if (!discovery.sso_enabled) {
        setProblem(
          `No identity provider is connected for ${domain}. Ask an owner of this organization how you should sign in.`,
        );
        setStarting(false);
        return;
      }
      const here = `${window.location.pathname}${window.location.search}`;
      window.location.assign(ssoStartHref(domain, sanitizeRedirectPath(here, "/workspaces")));
    } catch {
      setProblem("Single sign-on could not be started. Try again in a moment.");
      setStarting(false);
    }
  }, [email]);

  const chooseAnother = useCallback(() => {
    clear();
    window.location.assign("/workspaces");
  }, [clear]);

  if (!refusal) {
    return null;
  }

  const needsSso = refusal.code === "ORGANIZATION_SSO_REQUIRED";
  const needsMfa = refusal.code === "ORGANIZATION_MFA_REQUIRED";

  return (
    <div
      className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4"
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="org-policy-title"
        aria-describedby="org-policy-reason"
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 shadow-2xl"
      >
        <div className="flex items-start gap-3">
          <span
            aria-hidden="true"
            className="mt-0.5 flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-primary/10 text-primary"
          >
            {needsSso ? (
              <KeyRound className="h-5 w-5" />
            ) : needsMfa ? (
              <ShieldCheck className="h-5 w-5" />
            ) : (
              <ShieldOff className="h-5 w-5" />
            )}
          </span>
          <div className="min-w-0">
            <h2 id="org-policy-title" className="text-base font-semibold text-foreground">
              {needsSso
                ? "Single sign-on required"
                : needsMfa
                  ? "Two-factor sign-in required"
                  : "Not available from this network"}
            </h2>
            <p id="org-policy-reason" className="mt-1 text-sm text-muted-foreground">
              {refusal.message}
            </p>
          </div>
        </div>

        {needsMfa ? <MfaSetup /> : null}

        {problem ? (
          <p role="alert" className="mt-4 text-sm text-destructive">
            {problem}
          </p>
        ) : null}

        <div className="mt-6 flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
          <button
            type="button"
            onClick={chooseAnother}
            className="rounded-md border border-border px-3 py-2 text-sm hover:bg-muted"
          >
            Choose another organization
          </button>
          {needsSso ? (
            <button
              ref={primaryRef}
              type="button"
              onClick={() => void startSso()}
              disabled={starting}
              className="inline-flex items-center justify-center gap-2 rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground disabled:opacity-60"
            >
              {starting ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
              Sign in with single sign-on
            </button>
          ) : null}
        </div>
      </div>
    </div>
  );
};

/** The two-factor setup, then "Continue" (a reload) once the account has it on. */
const MfaSetup: React.FC = () => {
  // The panel refreshes this same query when the second factor is confirmed.
  const status = useQuery({ queryKey: MFA_STATUS_KEY, queryFn: getMfaStatus });
  return (
    <div className="mt-4 max-h-[60vh] overflow-y-auto">
      <Suspense
        fallback={<Loader2 className="mx-auto h-5 w-5 animate-spin text-muted-foreground" aria-hidden="true" />}
      >
        <TwoFactorPanel />
      </Suspense>
      {status.data?.enabled ? (
        <div className="mt-3 flex justify-end">
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="rounded-md bg-primary px-3 py-2 text-sm font-medium text-primary-foreground"
          >
            Continue
          </button>
        </div>
      ) : null}
    </div>
  );
};

export default OrganizationPolicyNotice;
