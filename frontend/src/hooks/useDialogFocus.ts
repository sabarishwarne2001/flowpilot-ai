import { useEffect, useRef } from "react";
import type { RefObject } from "react";

/**
 * What every modal dialog owes a keyboard and screen-reader user, in one place:
 *
 * - focus moves into the dialog when it opens (an element marked `data-autofocus`, else its first
 *   field, else its first button);
 * - Escape closes it (unless `busy`, so a request in flight is not orphaned);
 * - Tab and Shift+Tab stay inside it;
 * - focus returns to whatever opened it when it closes.
 *
 * ConfirmDialog has always done this by hand. Eleven other dialogs (API keys, webhooks, SCIM
 * tokens, seats, compliance erasure, audit details, warehouse destinations, supplier invoices,
 * workspace access, bulk delete, the assistant's pickers) did none of it: Escape did nothing and
 * Tab walked out into the page behind the overlay.
 */
const FOCUSABLE =
  "a[href], button:not([disabled]), input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export interface DialogFocusOptions {
  /** Whether the dialog is open. Default true (for dialogs that unmount when closed). */
  readonly open?: boolean;
  /** While true, Escape does nothing. */
  readonly busy?: boolean;
  /** Keep Tab inside the dialog. Default true; false for a non-modal popover. */
  readonly trap?: boolean;
  /** Move focus into the dialog on open. Default true. */
  readonly autoFocus?: boolean;
}

export function useDialogFocus(
  ref: RefObject<HTMLElement | null>,
  onClose: () => void,
  { open = true, busy = false, trap = true, autoFocus = true }: DialogFocusOptions = {},
): void {
  const onCloseRef = useRef(onClose);
  const busyRef = useRef(busy);
  useEffect(() => {
    onCloseRef.current = onClose;
    busyRef.current = busy;
  });

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const root = ref.current;
    if (autoFocus && root && !root.contains(document.activeElement)) {
      // An element marked data-autofocus wins (a destructive confirmation marks its Cancel), then
      // the first field, then the first control.
      const marked = root.querySelector<HTMLElement>("[data-autofocus]");
      const field = root.querySelector<HTMLElement>(
        "input:not([disabled]):not([type='hidden']), select:not([disabled]), textarea:not([disabled])",
      );
      const target = marked ?? field ?? root.querySelector<HTMLElement>(FOCUSABLE) ?? root;
      if (target === root && !root.hasAttribute("tabindex")) {
        root.setAttribute("tabindex", "-1");
      }
      target.focus();
    }

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        if (event.defaultPrevented) {
          return; // an inner widget (a select, a nested menu) handled it first
        }
        event.preventDefault();
        if (!busyRef.current) {
          onCloseRef.current();
        }
        return;
      }
      if (!trap || event.key !== "Tab" || !ref.current) {
        return;
      }
      const focusable = Array.from(ref.current.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
        (element) => element.offsetParent !== null || element === document.activeElement,
      );
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        event.preventDefault();
        return;
      }
      if (event.shiftKey && (document.activeElement === first || !ref.current.contains(document.activeElement))) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && (document.activeElement === last || !ref.current.contains(document.activeElement))) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      if (previous && document.contains(previous)) {
        previous.focus();
      }
    };
  }, [open, ref, trap, autoFocus]);
}

export default useDialogFocus;
