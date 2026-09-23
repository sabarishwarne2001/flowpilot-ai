import React, { useEffect, useId, useRef } from "react";
import { createPortal } from "react-dom";

/**
 * HM-S1:confirm-dialog — FlowPilot's modal confirmation.
 *
 * Every existing caller keeps working (the props are a superset). What changed
 * is behaviour a dialog owes its reader: it is announced as an alert dialog,
 * Escape cancels, focus moves into it on open and back to where it was on
 * close, Tab stays inside it, and it renders into document.body so no
 * ancestor's overflow or stacking context can clip it. The body is never
 * scroll-locked: hiding the scrollbar shifts the page underneath by its width.
 */
export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmText?: string;
  cancelText?: string;
  loading?: boolean;
  loadingText?: string;
  /** "danger" for destructive confirmations (the default), "primary" otherwise. */
  tone?: "danger" | "primary";
  /** Which button has focus when the dialog opens. Default: the safe one. */
  initialFocus?: "confirm" | "cancel";
  /** A single-button notice. */
  hideCancel?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

const FOCUSABLE = "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])";

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  open,
  title,
  message,
  confirmText = "Delete",
  cancelText = "Cancel",
  loading = false,
  loadingText,
  tone = "danger",
  initialFocus = "cancel",
  hideCancel = false,
  onConfirm,
  onCancel,
}) => {
  const titleId = useId();
  const messageId = useId();
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const confirmRef = useRef<HTMLButtonElement | null>(null);
  const cancelRef = useRef<HTMLButtonElement | null>(null);
  const onCancelRef = useRef(onCancel);
  const loadingRef = useRef(loading);

  useEffect(() => {
    onCancelRef.current = onCancel;
    loadingRef.current = loading;
  });

  useEffect(() => {
    if (!open) {
      return undefined;
    }
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const target = initialFocus === "confirm" || hideCancel ? confirmRef.current : cancelRef.current;
    target?.focus();

    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        if (!loadingRef.current) {
          onCancelRef.current();
        }
        return;
      }
      if (event.key !== "Tab" || dialogRef.current === null) {
        return;
      }
      const focusable = Array.from(dialogRef.current.querySelectorAll<HTMLElement>(FOCUSABLE));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (!first || !last) {
        return;
      }
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
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
  }, [open, initialFocus, hideCancel]);

  if (!open) {
    return null;
  }

  const busyLabel = loadingText ?? (confirmText === "Delete" ? "Deleting…" : "Working…");
  const confirmClass =
    tone === "danger"
      ? "bg-destructive text-white hover:opacity-90"
      : "bg-primary text-primary-foreground hover:bg-primary/90";

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex items-center justify-center bg-black/60 p-4 backdrop-blur-[1px]"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        className="w-full max-w-md rounded-xl border border-border bg-card p-6 text-card-foreground shadow-2xl"
      >
        <h2 id={titleId} className="text-lg font-semibold text-foreground">
          {title}
        </h2>

        <p id={messageId} className="mt-3 text-sm text-muted-foreground">
          {message}
        </p>

        <div className="mt-6 flex flex-wrap justify-end gap-3">
          {hideCancel ? null : (
            <button
              ref={cancelRef}
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="rounded-md border border-border px-4 py-2 text-sm font-medium text-foreground hover:bg-muted disabled:opacity-50"
            >
              {cancelText}
            </button>
          )}

          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={loading}
            className={`rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50 ${confirmClass}`}
          >
            {loading ? busyLabel : confirmText}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
};

export default ConfirmDialog;
