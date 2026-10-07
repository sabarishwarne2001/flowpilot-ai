import { create } from "zustand";
import { createJSONStorage, devtools, persist } from "zustand/middleware";

export type ThemeMode = "light" | "dark" | "system";

const UI_STORE_KEY = "flowpilot_ui_preferences";

const getSystemTheme = (): Exclude<ThemeMode, "system"> => {
  if (typeof window === "undefined") {
    return "light";
  }

  return window.matchMedia("(prefers-color-scheme: dark)").matches
    ? "dark"
    : "light";
};

export type ResolvedTheme = Exclude<ThemeMode, "system">;

export const resolveTheme = (theme: ThemeMode): ResolvedTheme =>
  theme === "system" ? getSystemTheme() : theme;

/** Applies the theme to the page and returns what it resolved to. public/theme-boot.js does the
 *  same before the app loads, so a dark-mode page is never painted light first. */
const applyTheme = (theme: ThemeMode): ResolvedTheme => {
  const resolved = resolveTheme(theme);
  if (typeof document !== "undefined") {
    document.documentElement.classList.toggle("dark", resolved === "dark");
  }
  return resolved;
};

interface UIState {
  readonly isSidebarCollapsed: boolean;
  readonly isMobileSidebarOpen: boolean;

  readonly theme: ThemeMode;
  /** What `theme` resolves to right now ("system" follows the computer). Icons and labels use it. */
  readonly resolvedTheme: ResolvedTheme;
  readonly notificationBadgeCount: number;

  readonly toggleSidebarCollapse: () => void;

  readonly openMobileSidebar: () => void;
  readonly closeMobileSidebar: () => void;
  readonly toggleMobileSidebar: () => void;

  readonly setTheme: (theme: ThemeMode) => void;
  readonly toggleTheme: () => void;
  readonly setNotificationBadgeCount: (count: number) => void;
  readonly clearNotificationBadge: () => void;
}

export const useUIStore = create<UIState>()(
  devtools(
    persist(
      (set, get) => ({
        isSidebarCollapsed: false,
        isMobileSidebarOpen: false,
        theme: "system",
        resolvedTheme: resolveTheme("system"),
        notificationBadgeCount: 0,

        toggleSidebarCollapse: () =>
          set(
            (state) => ({ isSidebarCollapsed: !state.isSidebarCollapsed }),
            false,
            "ui/toggleSidebarCollapse",
          ),

        openMobileSidebar: () =>
          set(
            { isMobileSidebarOpen: true },
            false,
            "ui/openMobileSidebar",
          ),

        closeMobileSidebar: () =>
          set(
            { isMobileSidebarOpen: false },
            false,
            "ui/closeMobileSidebar",
          ),

        toggleMobileSidebar: () =>
          set(
            (state) => ({ isMobileSidebarOpen: !state.isMobileSidebarOpen }),
            false,
            "ui/toggleMobileSidebar",
          ),

        setTheme: (theme) => {
          set(
            { theme, resolvedTheme: applyTheme(theme) },
            false,
            "ui/setTheme",
          );
        },

        // From what is on screen: "system" on a light computer goes to dark. It went to light,
        // so the first click did nothing.
        toggleTheme: () => {
          const next: ResolvedTheme = get().resolvedTheme === "dark" ? "light" : "dark";
          set(
            { theme: next, resolvedTheme: applyTheme(next) },
            false,
            "ui/toggleTheme",
          );
        },

        setNotificationBadgeCount: (count) =>
          set(
            (state) => {
              const next = Math.max(0, count);

              if (state.notificationBadgeCount === next) {
                return state;
              }

              return {
                notificationBadgeCount: next,
              };
            },
            false,
            "ui/setNotificationBadgeCount",
          ),

        clearNotificationBadge: () =>
          set(
            { notificationBadgeCount: 0 },
            false,
            "ui/clearNotificationBadge",
          ),
      }),
      {
        name: UI_STORE_KEY,
        storage: createJSONStorage(() => localStorage),
        partialize: (state) => ({
          isSidebarCollapsed: state.isSidebarCollapsed,
          theme: state.theme,
        }),
        onRehydrateStorage: () => (state) => {
          if (!state) {
            return;
          }
          const resolvedTheme = applyTheme(state.theme);
          queueMicrotask(() => useUIStore.setState({ resolvedTheme }));
        },
      },
    ),
    {
      name: "FlowPilotUIStore",
    },
  ),
);

if (typeof window !== "undefined") {
  window
    .matchMedia("(prefers-color-scheme: dark)")
    .addEventListener("change", () => {
      const theme = useUIStore.getState().theme;
      if (theme === "system") {
        useUIStore.setState({ resolvedTheme: applyTheme("system") });
      }
    });
}
