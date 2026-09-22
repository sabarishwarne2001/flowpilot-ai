/**
 * HARDENING-T2:Phase3 — warn before discarding unsaved settings.
 *
 * Covers reload, tab close and external navigation through `beforeunload`.
 * In-app route changes cannot be blocked here: the app mounts <BrowserRouter>,
 * and react-router's `useBlocker` only works under a data router
 * (createBrowserRouter). That migration is recorded in the certification.
 */
import { useEffect } from "react";

export function useUnsavedChangesGuard(isDirty: boolean): void {
  useEffect(() => {
    if (!isDirty) {
      return undefined;
    }
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault();
      // Required by some browsers to show the native prompt.
      event.returnValue = "";
    };
    window.addEventListener("beforeunload", handler);
    return () => window.removeEventListener("beforeunload", handler);
  }, [isDirty]);
}

export default useUnsavedChangesGuard;
