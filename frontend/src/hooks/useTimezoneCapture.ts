import { useEffect, useRef } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";

import { getMyProfile, offerDetectedTimezone } from "@/services/api/profile";
import { profileKeys } from "@/services/api/queryKeys";

/**
 * ARCH-30 Tranche 4 (A3) — offer the browser's IANA zone once, on first
 * authenticated load, when nobody has ever set one.
 *
 * WHY A HOOK ON THE LAYOUT AND NOT A CALL IN THE LOGIN HANDLER
 * -----------------------------------------------------------
 * Login is not the only way a session starts. A refresh, a restored tab, an
 * SSO redirect and an invitation acceptance all land on an authenticated page
 * without passing through the password form. Hanging this off the layout every
 * authenticated route renders is the only placement that covers all of them.
 *
 * WHY IT READS THE PROFILE ITSELF
 * -------------------------------
 * `profileKeys.me()` is the same key the settings screen and the display
 * preferences boundary already use, so on most boots this resolves from cache
 * and costs nothing. Taking the profile as a prop would have forced the layout
 * to fetch it, which is a request added to every page for the benefit of the
 * small minority of sessions that have not chosen a timezone.
 *
 * WHAT IT WILL NOT DO
 * -------------------
 * It will not overwrite a timezone somebody chose. The decision is the
 * server's — `POST /me/profile/detected-timezone` refuses unless
 * `timezone_source` is still DEFAULT — and this hook additionally declines to
 * send unless the profile it already holds says DEFAULT. Two checks for one
 * rule is not redundancy: the client-side one keeps a write request off every
 * page load for the overwhelming majority of sessions, and the server-side one
 * is the load-bearing one, because a client can be stale or hostile.
 *
 * It will not retry. `attempted` is a ref, so a re-render does not reset it,
 * and a failure is swallowed: a timezone that could not be captured is
 * cosmetic — timestamps render in UTC, labelled UTC, which is honest — and is
 * not worth a toast, a retry storm, or a boot-time error boundary. Profile
 * settings remains the authoritative path and always has been.
 *
 * `Intl.DateTimeFormat().resolvedOptions().timeZone` returns an IANA key in
 * every browser this app supports. On a runtime that returns undefined or a
 * non-IANA string the server's validator rejects it with 422 and this hook
 * does nothing further — the same outcome as not having tried.
 */
export const useTimezoneCapture = (): void => {
  const queryClient = useQueryClient();
  const attempted = useRef(false);

  const { data: profile } = useQuery({
    queryKey: profileKeys.me(),
    queryFn: getMyProfile,
    staleTime: 300_000,
    retry: false,
  });

  useEffect(() => {
    if (attempted.current || !profile) {
      return;
    }
    if (profile.timezone_source !== "DEFAULT") {
      return;
    }

    let detected: string | undefined;
    try {
      detected = Intl.DateTimeFormat().resolvedOptions().timeZone;
    } catch {
      detected = undefined;
    }
    if (!detected || detected === profile.timezone) {
      return;
    }

    attempted.current = true;

    void offerDetectedTimezone(detected)
      .then((result) => {
        if (result.adopted) {
          // Invalidate rather than setQueryData: the display preferences
          // boundary reads the profile through this same cache, so
          // re-fetching is how every subscriber picks the new zone up in one
          // place instead of each of them having to be told.
          void queryClient.invalidateQueries({ queryKey: profileKeys.me() });
        }
      })
      .catch(() => {
        // Deliberately silent. See the docblock.
      });
  }, [profile, queryClient]);
};

export default useTimezoneCapture;
