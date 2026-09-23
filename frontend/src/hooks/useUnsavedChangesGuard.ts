/**
 * HARDENING-T2/FINAL/MASTER — warn before discarding unsaved settings.
 *
 * Two layers:
 *   * react-router's `useBlocker` for in-app navigation (sidebar, links,
 *     back/forward). A blocked navigation is published to a small store and
 *     answered by FlowPilot's own dialog (`UnsavedChangesDialogHost`, mounted
 *     once in App.tsx), never by the browser's unstyled native confirm box.
 *   * `beforeunload` for reload, tab close and external navigation. Browsers
 *     show their own prompt there and allow no custom UI, by design: a page
 *     that could restyle the leave prompt could also make it lie.
 *
 * Pages call `useUnsavedChangesGuard(isDirty)` and render nothing extra.
 */
import { useEffect } from "react";
import { useBlocker } from "react-router-dom";
import { create } from "zustand";

export const UNSAVED_CHANGES_TITLE = "Unsaved Changes";
export const UNSAVED_CHANGES_MESSAGE =
  "You have unsaved changes in this form. If you navigate away now, your edits will be discarded.";
export const UNSAVED_CHANGES_LEAVE = "Discard & Leave";
export const UNSAVED_CHANGES_STAY = "Stay on Page";

interface PendingNavigation {
  readonly proceed: () => void;
  readonly reset: () => void;
}

interface NavigationGuardState {
  readonly pending: PendingNavigation | null;
  readonly setPending: (pending: PendingNavigation | null) => void;
}

export const useNavigationGuardStore = create<NavigationGuardState>((set) => ({
  pending: null,
  setPending: (pending) => set({ pending }),
}));

export function useUnsavedChangesGuard(isDirty: boolean): void {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => isDirty && currentLocation.pathname !== nextLocation.pathname,
  );
  const setPending = useNavigationGuardStore((state) => state.setPending);

  useEffect(() => {
    if (blocker.state === "blocked") {
      setPending({ proceed: () => blocker.proceed(), reset: () => blocker.reset() });
      return;
    }
    setPending(null);
  }, [blocker, setPending]);

  useEffect(() => () => setPending(null), [setPending]);

  useEffect(() => {
    if (!isDirty) {
      return undefined;
    }
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);
}

export default useUnsavedChangesGuard;
