/**
 * ARCH-29 Tranche 1 — the reader for user avatar data.
 *
 * WHY THIS COMPONENT EXISTS
 * =========================
 *
 * Before this file, `avatar_url` appeared in exactly two places in `src/`:
 * `types/api.generated.d.ts` and `pages/Settings/ProfileSettings.tsx` — the
 * form that uploads it. A user could set an avatar and it would render only
 * on the page where they set it. Every other surface that shows who someone
 * is — the sidebar footer, member lists, audit views — showed an email string.
 *
 * That is the ORPHANED GUARD defect (invariant I4) with the polarity flipped:
 * not logic with no call sites, but DATA WITH NO READERS. The invisibility
 * mechanism is identical. TypeScript does not flag a field that is written and
 * never read, Python does not flag a column that is populated and never
 * selected, and no test fails because nothing asserts that a byte written to
 * object storage is ever fetched back. `verify_arch29_tranche1.py` G3 closes
 * it by asserting this component has call sites, exactly as ARCH-28 G3 does
 * for the XSW defences.
 *
 * WHY IT TAKES A userId AND NOT A URL
 * ===================================
 *
 * There is no `avatar_url` column. `PROFILE_ENDPOINTS.userAvatar(id)` derives
 * `/users/{id}/avatar`, and the object is served — or 404s — behind the same
 * authorization as every other read. Passing a URL in would let a caller point
 * this component at an arbitrary path and have `useAuthenticatedImage` fetch
 * it with the session's credentials. Taking an id means the only thing a
 * caller can request is an avatar.
 *
 * WHY A 404 IS NOT AN ERROR
 * =========================
 *
 * Most users have no avatar. `useAuthenticatedImage` catches the rejection and
 * resolves to null, which falls through to initials. "No avatar" and "avatar
 * failed to load" are deliberately indistinguishable here: both mean render
 * the fallback, and a broken-image icon on the sidebar of every user who never
 * uploaded a photo would be worse than either.
 *
 * WHY `version` EXISTS
 * ====================
 *
 * The object lives at a stable path, so a browser that has cached it will keep
 * showing the old photo after an upload. `ProfileSettings` already solves this
 * for itself with a `?v=` counter. Threading the same counter through here
 * lets a caller that has just uploaded force the refetch. Callers that only
 * display — the sidebar footers — omit it and take the cached copy, which is
 * the correct trade for a surface that renders on every page.
 */

import React from "react";

import { useAuthenticatedImage } from "@/hooks/useAuthenticatedImage";
import { PROFILE_ENDPOINTS } from "@/services/api/endpoints";
import { useAvatarVersion } from "@/store/useAvatarVersionStore";

export type AvatarSize = "xs" | "sm" | "md" | "lg";

interface AvatarProps {
  /**
   * The user whose avatar to fetch. Null or undefined renders the fallback
   * without making a request — the signed-out and still-loading cases.
   */
  readonly userId: string | null | undefined;

  /**
   * Used for initials and for the accessible label. Email is the fallback
   * because it is the one identifier every user in this system has.
   *
   * `| undefined` is spelled out on every optional prop below because this
   * project compiles with `exactOptionalPropertyTypes`. Under that flag an
   * optional property does NOT implicitly admit `undefined`, so `email?:
   * string | null` rejects `user?.email` — which is exactly the shape every
   * call site has, since `user` is nullable in the auth store. Omitting it
   * compiles the component fine and fails at each consumer, which is the
   * worst place to discover it.
   */
  readonly email?: string | null | undefined;

  /**
   * Preferred over `email` for initials when present.
   */
  readonly displayName?: string | null | undefined;

  readonly size?: AvatarSize | undefined;

  /**
   * Cache-buster. Supply only after an upload; see the module docstring.
   */
  readonly version?: number | string | undefined;

  readonly className?: string | undefined;
}

const SIZE_CLASSES: Record<AvatarSize, string> = {
  xs: "h-6 w-6 text-[10px]",
  sm: "h-8 w-8 text-xs",
  md: "h-10 w-10 text-sm",
  lg: "h-12 w-12 text-base",
};

/**
 * Two characters at most, derived from a display name if there is one and from
 * the local part of an email if there is not.
 *
 * Splitting the email's local part on `.`, `_`, `-` and `+` turns
 * `sabarish.warne@example.com` into "SW" rather than "SA". The `+` is included
 * because plus-addressing is common and `user+flowpilot@…` should not initial
 * as "UF".
 */
export function initialsFor(
  displayName?: string | null,
  email?: string | null,
): string {
  const fromName = (displayName ?? "").trim();
  if (fromName.length > 0) {
    return fromName
      .split(/\s+/)
      .slice(0, 2)
      .map((word) => word.charAt(0).toUpperCase())
      .join("");
  }

  const localPart = (email ?? "").trim().split("@")[0] ?? "";
  if (localPart.length > 0) {
    const parts = localPart.split(/[._\-+]+/).filter(Boolean);
    // Destructured rather than indexed: `noUncheckedIndexedAccess` is on, so
    // `parts[0]` is `string | undefined` even inside a length check, and the
    // compiler is right to insist — `.filter(Boolean)` does not narrow the
    // element type.
    const [first, second] = parts;
    if (first !== undefined && second !== undefined) {
      return first.charAt(0).toUpperCase() + second.charAt(0).toUpperCase();
    }
    return localPart.slice(0, 2).toUpperCase();
  }

  return "?";
}

export const Avatar: React.FC<AvatarProps> = ({
  userId,
  email = null,
  displayName = null,
  size = "sm",
  version,
  className = "",
}) => {
  // ARCH-29 Tranche 3. The shared version, so an upload in ProfileSettings
  // repaints every mounted Avatar for this user rather than only the one on
  // the page that performed it. An explicit `version` prop still wins, for the
  // caller that is driving its own preview.
  const sharedVersion = useAvatarVersion(userId);
  const effectiveVersion = version ?? (sharedVersion > 0 ? sharedVersion : undefined);

  const path = userId
    ? `${PROFILE_ENDPOINTS.userAvatar(userId)}${
        effectiveVersion === undefined ? "" : `?v=${effectiveVersion}`
      }`
    : null;

  const src = useAuthenticatedImage(path);

  const initials = initialsFor(displayName, email);
  const label = displayName ?? email ?? "User";
  const sizeClass = SIZE_CLASSES[size];

  return (
    <span
      className={`
        inline-flex
        flex-shrink-0
        items-center
        justify-center
        overflow-hidden
        rounded-full
        border
        border-border
        bg-muted
        font-semibold
        text-muted-foreground
        select-none
        ${sizeClass}
        ${className}
      `}
      // The image carries the identity, so the wrapper is decorative when the
      // image loads and carries the label when it does not.
      role="img"
      aria-label={`${label} avatar`}
      title={label}
    >
      {src ? (
        <img
          src={src}
          alt=""
          aria-hidden="true"
          className="h-full w-full object-cover"
        />
      ) : (
        <span aria-hidden="true">{initials}</span>
      )}
    </span>
  );
};

export default Avatar;
