import { create } from "zustand";
import {
  createJSONStorage,
  devtools,
  persist,
} from "zustand/middleware";

/**
 * Storage key for persisted authentication state.
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
         * Clears all authentication state.
         */
        clearAuth: () =>
          set({
            user: null,
            token: null,
            isAuthenticated: false,
          }),

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

        partialize: (state) => ({
          user: state.user,
          token: state.token,
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
