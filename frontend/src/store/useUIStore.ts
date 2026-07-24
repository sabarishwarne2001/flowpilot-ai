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

const applyTheme = (theme: ThemeMode): void => {
  if (typeof document === "undefined") {
    return;
  }

  const resolved = theme === "system" ? getSystemTheme() : theme;

  document.documentElement.classList.toggle("dark", resolved === "dark");
};

interface UIState {
  readonly isSidebarCollapsed: boolean;
  readonly isMobileSidebarOpen: boolean;

  readonly theme: ThemeMode;
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
          applyTheme(theme);
          set(
            { theme },
            false,
            "ui/setTheme",
          );
        },

        toggleTheme: () => {
          const current = get().theme;
          const next = current === "light" ? "dark" : "light";
          applyTheme(next);
          set(
            { theme: next },
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
          applyTheme(state.theme);
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
        applyTheme("system");
      }
    });
}
