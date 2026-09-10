/**
 * ARCH-29 Tranche 1 — one place where a guard decides what login path to send
 * a turned-away user to.
 *
 * WHY A HOOK AND NOT FIVE CALL SITES READING THE STORE
 * ====================================================
 *
 * `loginPathWithRedirect` had six call sites across five guards: `PrivateRoute`
 * (twice), `SuperAdminGuard`, `TenantGuard`, `LegacyRouteRedirect` and
 * `WorkspacePicker`. Adding an `isSigningOut` read to each is five chances to
 * write the condition backwards and five files to re-audit when a sixth guard
 * ships. The security-relevant sibling of this decision — open-redirect
 * validation — is already centralised in `isSafeRedirectPath` for exactly that
 * reason, and this follows the same shape.
 *
 * `verify_arch29_tranche1.py` G5 asserts that no guard calls
 * `loginPathWithRedirect` directly any more. That check is what stops the
 * sixth guard from quietly reintroducing the defect: a new file that reaches
 * for the obvious-looking function fails the gate rather than shipping a
 * surface where sign-out remembers where you were.
 */

import { useAuthStore } from "@/store/useAuthStore";
import { loginPathForExit } from "@/routes/tenantPaths";

/**
 * Returns the login path for the given destination, honouring whether the user
 * is in the middle of a deliberate sign-out.
 *
 * Callers pass the destination they would have preserved. Whether it survives
 * is not their decision.
 */
export function useLoginRedirect(destination: string): string {
  const isSigningOut = useAuthStore((state) => state.isSigningOut);
  return loginPathForExit(destination, isSigningOut);
}

export default useLoginRedirect;
