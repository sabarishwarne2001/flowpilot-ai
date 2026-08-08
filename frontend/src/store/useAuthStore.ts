import { create } from "zustand";
import {
  createJSONStorage,
  devtools,
  persist,
} from "zustand/middleware";
import { useTenantStore } from "@/store/useTenantStore";

/**
 * Storage key for persisted authentication state.
 *
 * ARCH-03 Step 7: the access token is NO LONGER persisted here.
 *
 * The point of moving the long-lived credential into an HttpOnly cookie is
 * that injected script cannot reach it. Leaving the access token in
 * localStorage keeps a readable credential on disk and hands most of that
 * property back — a stolen token would still be usable, and it would survive
 * closing the tab.
 *
 * The token now lives in memory for the life of the page. What survives a
 * reload is the cookie, which the browser presents to /auth/refresh during
 * startup. `user` and `hasSession` are still persisted, purely so the first
 * paint after a reload can show the authenticated shell instead of a login
 * flash; neither is a credential and neither is trusted by the server.
 */
const AUTH_STORAGE_KEY = "flowpilot_auth_session";

/**
 * Mirrors the backend User schema.
 */
export interface User {
  readonly id: string;
  readonly email: string;
  readonly is_active: boolean;
  readonly is_superuser: boolean;
  readonly created_at: string;
  readonly updated_at: string;

  /**
   * When this address was proved, or null if it has not been.
   *
   * null is a permanent, meaningful value rather than a missing field: the
   * account works, it simply cannot reach a workspace yet (ARCH-03 §B.4).
   */
  readonly email_verified_at: string | null;
}

interface AuthState {
  readonly user: User | null;
  readonly token: string | null;
  readonly isAuthenticated: boolean;

  /**
   * Stores only the JWT token.
   *
   * Used immediately after login so authenticated
   * requests (such as /auth/me) can execute before
   * the full session is established.
   */
  readonly setToken: (
    token: string,
  ) => void;

  /**
   * Persists the authenticated user session.
   */
  readonly setAuth: (
    user: User,
    token: string,
  ) => void;

  /**
   * Clears the authenticated session.
   */
  readonly clearAuth: () => void;

  /**
   * Drops the cached user without ending the session.
   *
   * Used after verification: the server's gate reads the User row rather than
   * a token claim, so access changes immediately — but the copy held here
   * still says unverified, and the banner would linger over an account that
   * is fine. Clearing forces the next /auth/me to repopulate it.
   */
  readonly clearUserCache: () => void;

  /**
   * Simple role helper.
   */
  readonly hasRole: (
    role: "superuser",
  ) => boolean;
}

export const useAuthStore = create<AuthState>()(
  devtools(
    persist(
      (set, get) => ({
        user: null,
        token: null,
        isAuthenticated: false,

        /**
         * Stores only the access token.
         *
         * This is intentionally separated from setAuth()
         * because the application performs:
         *
         * Login
         *   ↓
         * Receive JWT
         *   ↓
         * GET /auth/me
         *   ↓
         * setAuth(user, token)
         */
        setToken: (token) =>
          set((state) => ({
            ...state,
            token,
            // A token obtained from /auth/refresh at startup proves the
            // session is live, so the flag follows it. Without this a
            // restored session would hold a working token while every guard
            // still believed the user was signed out.
            isAuthenticated: true,
          })),

        /**
         * Stores the complete authenticated session.
         */
        setAuth: (user, token) =>
          set({
            user,
            token,
            isAuthenticated: true,
          }),

        /**
         * Clears all authentication state and the tenant selection.
         *
         * The tenant reset belongs here rather than at each call site because
         * clearAuth is reached from three places — explicit sign-out, the 401
         * interceptor, and a failed login — and a path that forgot it would
         * leave the next user on this machine starting from a stranger's
         * tenant identifiers.
         *
         * One-way dependency: the tenant store imports nothing from here, so
         * there is no cycle.
         */
        clearAuth: () => {
          useTenantStore.getState().resetTenantSelection();

          set({
            user: null,
            token: null,
            isAuthenticated: false,
          });
        },

        clearUserCache: () =>
          set((state) => ({
            ...state,
            user: null,
          })),

        /**
         * Authorization helper.
         */
        hasRole: (role) => {
          const user = get().user;

          if (!user) {
            return false;
          }

          switch (role) {
            case "superuser":
              return user.is_superuser;

            default:
              return false;
          }
        },
      }),
      {
        name: AUTH_STORAGE_KEY,

        storage: createJSONStorage(
          () => localStorage,
        ),

        // token is deliberately absent. Adding it back would undo the
        // XSS-exposure reduction that the refresh cookie exists to provide.
        partialize: (state) => ({
          user: state.user,
          isAuthenticated:
            state.isAuthenticated,
        }),
      },
    ),
    {
      name: "AuthStore",
    },
  ),
);
