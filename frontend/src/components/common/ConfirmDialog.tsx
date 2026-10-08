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
  /**
   * Phase 3. A text the action needs (a payment reference, a reason), asked for inside the
   * dialog instead of `window.prompt`, whose Cancel still let the action run. The dialog is then
   * a form dialog (role "dialog"), focus starts in the field and Enter confirms.
   */
  input?: ConfirmDialogInput | undefined;
  /** Keeps the confirm button disabled (for example while `input` is not valid yet). */
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export interface ConfirmDialogInput {
  readonly label: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly placeholder?: string;
  readonly hint?: string;
  readonly maxLength?: number;
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
  input,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}) => {
  const titleId = useId();
  const messageId = useId();
  const inputId = useId();
  const inputRef = useRef<HTMLInputElement | null>(null);
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
    const target = inputRef.current
      ?? (initialFocus === "confirm" || hideCancel ? confirmRef.current : cancelRef.current);
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
      ? "fp-btn-danger"
      : "fp-btn-primary";

  return createPortal(
    <div
      className="fixed inset-0 z-[100] flex animate-fade-in items-center justify-center bg-black/60 p-4 backdrop-blur-sm"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !loading) {
          onCancel();
        }
      }}
    >
      <div
        ref={dialogRef}
        role={input ? "dialog" : "alertdialog"}
        aria-modal="true"
        aria-labelledby={titleId}
        aria-describedby={messageId}
        className="w-full max-w-md animate-scale-in rounded-2xl border border-border-strong/60 bg-popover p-6 text-popover-foreground shadow-elevation-3"
      >
        <h2 id={titleId} className="text-base font-semibold tracking-tight text-foreground">
          {title}
        </h2>

        <p id={messageId} className="mt-2 text-sm leading-relaxed text-muted-foreground">
          {message}
        </p>

        {input ? (
          <div className="mt-4 space-y-1.5">
            <label htmlFor={inputId} className="text-sm font-medium text-foreground">
              {input.label}
            </label>
            <input
              ref={inputRef}
              id={inputId}
              type="text"
              autoComplete="off"
              className="fp-input"
              value={input.value}
              placeholder={input.placeholder}
              maxLength={input.maxLength}
              disabled={loading}
              onChange={(event) => input.onChange(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === "Enter" && !confirmDisabled && !loading) {
                  event.preventDefault();
                  onConfirm();
                }
              }}
            />
            {input.hint ? <p className="text-xs leading-relaxed text-muted-foreground">{input.hint}</p> : null}
          </div>
        ) : null}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          {hideCancel ? null : (
            <button
              ref={cancelRef}
              type="button"
              onClick={onCancel}
              disabled={loading}
              className="fp-btn fp-btn-secondary h-9 px-4"
            >
              {cancelText}
            </button>
          )}

          <button
            ref={confirmRef}
            type="button"
            onClick={onConfirm}
            disabled={loading || confirmDisabled}
            className={`fp-btn h-9 px-4 font-semibold ${confirmClass}`}
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
