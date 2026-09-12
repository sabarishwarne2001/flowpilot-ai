import React, { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";

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
 * and a subtree that rendered before the profile arrived would otherwise keep
 * the browser's clock until something else made it re-render.
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

  const timeZone = profile.data?.timezone;
  const locale = profile.data?.locale;
  const preferenceKey = useMemo(
    () => applyDisplayPreferences(timeZone, locale),
    [timeZone, locale],
  );

  return <React.Fragment key={preferenceKey}>{children}</React.Fragment>;
};

export default DisplayPreferencesBoundary;
