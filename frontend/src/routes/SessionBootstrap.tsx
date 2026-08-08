/**
 * Restores a session from the refresh cookie before the router renders.
 *
 * As of ARCH-03 Step 7 the access token lives in memory only, so a reload
 * starts with no credential in hand. What survives is the HttpOnly refresh
 * cookie, and one call to /auth/refresh turns it back into a working token.
 *
 * WHY THIS EXISTS RATHER THAN LETTING THE 401 INTERCEPTOR HANDLE IT
 * -----------------------------------------------------------------
 * The interceptor would eventually get there: the first authenticated request
 * 401s, refresh runs, the request is retried. But every guard beneath the
 * router reacts to that first 401 before the retry resolves, so the user sees
 * a flash of the login screen on every reload of a perfectly live session.
 *
 * Doing it once, up front, makes the reload path deterministic. The
 * interceptor stays for mid-session expiry, which is the case it is actually
 * good at.
 *
 * The persisted `isAuthenticated` flag is used only to decide whether the
 * attempt is worth making. It says "this browser once held a session", which
 * is exactly the right question here and not enough to render anything on.
 */

import React from "react";

import { LoadingScreen } from "@/components/common/LoadingScreen";
import { restoreSession } from "@/services/api/client";
import { useAuthStore } from "@/store/useAuthStore";

interface SessionBootstrapProps {
  readonly children: React.ReactNode;
}

export function SessionBootstrap({ children }: SessionBootstrapProps) {
  const token = useAuthStore((state) => state.token);
  const hadSession = useAuthStore((state) => state.isAuthenticated);

  const [settled, setSettled] = React.useState<boolean>(
    () => Boolean(token) || !hadSession,
  );

  React.useEffect(() => {
    if (settled) {
      return;
    }

    let cancelled = false;

    // restoreSession never rejects: "not signed in" is an ordinary answer at
    // startup. It clears local state itself when there is nothing to restore.
    void restoreSession().finally(() => {
      if (!cancelled) {
        setSettled(true);
      }
    });

    return () => {
      cancelled = true;
    };
  }, [settled]);

  if (!settled) {
    return <LoadingScreen />;
  }

  return <>{children}</>;
}

export default SessionBootstrap;
