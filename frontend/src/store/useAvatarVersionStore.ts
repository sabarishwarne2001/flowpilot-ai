/**
 * ARCH-29 Tranche 3 — making an avatar change visible everywhere at once.
 *
 * THE PROBLEM
 * ===========
 *
 * `ProfileSettings` uploads an avatar and bumps its OWN local `?v=` counter, so
 * the preview on that page updates. Every other mounted `<Avatar>` — the
 * desktop sidebar, the mobile sidebar, the organization sidebar — is still
 * requesting the same URL it requested on mount, gets the browser's cached
 * copy, and shows the old photo until a full page reload.
 *
 * Deleting an avatar is worse: the sidebars keep displaying a photo that no
 * longer exists on the server.
 *
 * WHY A STORE AND NOT A QUERY INVALIDATION
 * ========================================
 *
 * `useAuthenticatedImage` is not a TanStack query — it is a `useEffect` that
 * fetches a blob and holds an object URL. There is no query key to invalidate.
 * The effect re-runs on one thing only: a change to its `url` argument.
 *
 * So the version has to be part of the URL, and it has to be shared. This store
 * is that shared value. `bumpAvatarVersion(userId)` changes the number, every
 * `<Avatar>` for that user recomputes its URL, and every effect re-runs. One
 * write, all consumers.
 *
 * Keyed BY USER ID rather than a single global counter, so bumping your own
 * avatar does not force a refetch of every other face rendered in a member
 * list.
 *
 * NOT PERSISTED
 * =============
 *
 * Deliberately in-memory. The version exists to defeat the browser's HTTP cache
 * within a session; a value carried across reloads would pin the URL to a stale
 * number forever and defeat normal caching in the other direction.
 */

import { create } from "zustand";

interface AvatarVersionState {
  /** userId -> monotonically increasing cache-buster. */
  readonly versions: Readonly<Record<string, number>>;
  readonly bump: (userId: string) => void;
}

export const useAvatarVersionStore = create<AvatarVersionState>()((set) => ({
  versions: {},
  bump: (userId: string) =>
    set((state) => ({
      versions: {
        ...state.versions,
        [userId]: (state.versions[userId] ?? 0) + 1,
      },
    })),
}));

/**
 * Signal that this user's avatar changed. Call after a successful upload OR
 * delete — a delete that does not bump leaves every sidebar showing a photo
 * the server no longer has.
 */
export function bumpAvatarVersion(userId: string | null | undefined): void {
  if (!userId) {
    return;
  }
  useAvatarVersionStore.getState().bump(userId);
}

/**
 * The current version for a user, or 0.
 *
 * Zero is meaningful: it means "not changed this session", and `<Avatar>`
 * omits the `?v=` parameter entirely in that case so the first paint after a
 * page load can be served from the browser cache. Always appending a version
 * would guarantee a cache miss on every reload — trading the stale-image bug
 * for the blank-flash bug this same tranche is fixing.
 */
export function useAvatarVersion(userId: string | null | undefined): number {
  return useAvatarVersionStore((state) =>
    userId ? state.versions[userId] ?? 0 : 0,
  );
}
