/**
 * HARDENING-T2/FINAL — warn before discarding unsaved settings.
 *
 * Two layers:
 *   * `beforeunload` for reload, tab close and external navigation;
 *   * react-router's `useBlocker` for in-app navigation (sidebar, links,
 *     back/forward). This needs a data router, which App.tsx now mounts
 *     (createBrowserRouter + RouterProvider).
 */
import { useEffect } from "react";
import { useBlocker } from "react-router-dom";

export const UNSAVED_CHANGES_PROMPT = "You have unsaved changes. Leave this page and discard them?";

export function useUnsavedChangesGuard(isDirty: boolean): void {
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) => isDirty && currentLocation.pathname !== nextLocation.pathname,
  );

  useEffect(() => {
    if (blocker.state !== "blocked") {
      return;
    }
    if (window.confirm(UNSAVED_CHANGES_PROMPT)) {
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker]);

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
