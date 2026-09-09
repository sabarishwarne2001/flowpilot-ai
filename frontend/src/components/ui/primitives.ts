/**
 * ARCH-29 Slice 4 — the canonical class strings for this design system.
 */

export const SURFACE =
  "rounded-xl border border-border/60 bg-card text-card-foreground shadow-sm";

export const SURFACE_GLASS =
  "rounded-xl border border-border/60 bg-card/60 text-card-foreground shadow-sm backdrop-blur-md";

export const SURFACE_DIALOG =
  "rounded-xl border border-border/60 bg-popover text-popover-foreground shadow-2xl";

export const SURFACE_INSET = "rounded-lg border border-border/60 bg-muted/40";

export const OVERLAY = "fp-overlay";

export const INPUT =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground disabled:opacity-60";

export const INPUT_MONO = `${INPUT} font-mono`;

export const TEXTAREA = `${INPUT} min-h-[5rem] resize-y`;

export const SELECT =
  "w-full rounded-lg border border-border bg-background px-3 py-2 text-sm text-foreground disabled:opacity-60";

export const BUTTON_PRIMARY =
  "inline-flex items-center justify-center gap-1.5 rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-colors hover:bg-primary/90 disabled:opacity-60";

export const BUTTON_SECONDARY =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-border bg-transparent px-3 py-1.5 text-sm font-semibold text-foreground transition-colors hover:bg-muted disabled:opacity-60";

export const BUTTON_GHOST =
  "inline-flex items-center justify-center gap-1.5 rounded-lg px-2.5 py-1.5 text-sm font-medium text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:opacity-60";

export const BUTTON_DESTRUCTIVE =
  "inline-flex items-center justify-center gap-1.5 rounded-lg border border-destructive/50 bg-transparent px-3 py-1.5 text-sm font-semibold text-destructive transition-colors hover:bg-destructive/10 disabled:opacity-60";

export const PAGE_TITLE = "text-xl font-semibold tracking-tight text-foreground";
export const SECTION_TITLE = "text-base font-semibold text-foreground";
export const FIELD_LABEL = "text-sm font-medium text-foreground";
export const HINT = "text-xs text-muted-foreground";

export const TABLE_HEAD =
  "border-b border-border/60 text-left text-xs font-medium text-muted-foreground";

export const TABLE_ROW =
  "border-b border-border/40 last:border-0 transition-colors hover:bg-muted/30";

export const SCROLL_X = "overflow-x-auto overscroll-x-contain";
