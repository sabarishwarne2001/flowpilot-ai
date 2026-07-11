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
  readonly isSidebarOpen: boolean;
  readonly theme: ThemeMode;
  readonly notificationBadgeCount: number;
  readonly toggleSidebar: () => void;
  readonly setSidebarOpen: (open: boolean) => void;
  readonly setTheme: (theme: ThemeMode) => void;
  readonly toggleTheme: () => void;
  readonly setNotificationBadgeCount: (count: number) => void;
  readonly clearNotificationBadge: () => void;
}

export const useUIStore = create<UIState>()(
  devtools(
    persist(
      (set, get) => ({
        isSidebarOpen: true,
        theme: "system",
        notificationBadgeCount: 0,

        toggleSidebar: () =>
          set(
            (state) => ({ isSidebarOpen: !state.isSidebarOpen }),
            false,
            "ui/toggleSidebar",
          ),

        setSidebarOpen: (open) =>
          set(
            { isSidebarOpen: open },
            false,
            "ui/setSidebarOpen",
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
          isSidebarOpen: state.isSidebarOpen,
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
