import React, { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

import LoadingScreen from "@/components/common/LoadingScreen";
import { getMyProfile } from "@/services/api/profile";
import { profileKeys } from "@/services/api/queryKeys";
import { useAuthStore } from "@/store/useAuthStore";
import { applyDisplayPreferences } from "@/utils/displayTime";

/**
 * ARCH-30 Tranche 3 (D-5). Applies the signed-in person's display timezone and
 * language to every timestamp formatted beneath it.
 *
 * It reads the same query key `ProfileSettings` writes on save, so changing the
 * timezone there updates the whole console without a reload. Children are
 * re-keyed when the preferences change: timestamps are formatted during render,
 * and a subtree that rendered before the change would otherwise keep the old
 * clock until something else made it re-render.
 *
 * F-121. The console waits for the profile before rendering. It used to render
 * at once with the browser's clock and then re-key when the profile arrived,
 * which (every user has a saved time zone) threw away and rebuilt every page on
 * every load: whatever was typed or selected in that moment was lost, and every
 * page fetched its data twice. A failed profile read falls back to the browser.
 */
export const DisplayPreferencesBoundary: React.FC<{ readonly children: React.ReactNode }> = ({
  children,
}) => {
  const isAuthenticated = useAuthStore((state) => state.isAuthenticated);
  const profile = useQuery({
    queryKey: profileKeys.me(),
    queryFn: getMyProfile,
    enabled: isAuthenticated,
    staleTime: 5 * 60_000,
  });

  const waitingForProfile = isAuthenticated && profile.isPending && !profile.isError;

  const timeZone = profile.data?.timezone;
  const locale = profile.data?.locale;
  const preferenceKey = useMemo(
    () => applyDisplayPreferences(timeZone, locale),
    [timeZone, locale],
  );

  if (waitingForProfile) {
    return <LoadingScreen />;
  }

  return <React.Fragment key={preferenceKey}>{children}</React.Fragment>;
};

export default DisplayPreferencesBoundary;
