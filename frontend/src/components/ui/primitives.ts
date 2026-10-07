/**
 * ARCH-29 Slice 4 — the canonical class strings for this design system.
 *
 * The `fp-*` classes are defined in src/styles/index.css (Tailwind's
 * components layer), so a utility added next to one of these strings
 * (`${BUTTON_PRIMARY} w-full`, `${INPUT} pl-8`) still wins.
 */

export const SURFACE = "fp-card";

export const SURFACE_GLASS =
  "rounded-xl border border-border/70 fp-glass text-card-foreground shadow-elevation-1";

export const SURFACE_DIALOG =
  "rounded-xl border border-border-strong/60 bg-popover text-popover-foreground shadow-elevation-3";

export const SURFACE_INSET = "rounded-lg border border-border/70 bg-muted/40";

export const OVERLAY = "fp-overlay";

export const INPUT = "fp-input";

export const INPUT_MONO = `${INPUT} font-mono`;

export const TEXTAREA = `${INPUT} min-h-[5rem] resize-y`;

export const SELECT = "fp-input";

export const BUTTON_PRIMARY = "fp-btn fp-btn-primary";

export const BUTTON_SECONDARY = "fp-btn fp-btn-secondary";

export const BUTTON_GHOST = "fp-btn fp-btn-ghost px-2.5";

export const BUTTON_DESTRUCTIVE = "fp-btn fp-btn-destructive";

export const PAGE_TITLE = "text-xl font-semibold tracking-tight text-foreground";
export const SECTION_TITLE = "text-[15px] font-semibold tracking-tight text-foreground";
export const FIELD_LABEL = "text-sm font-medium text-foreground";
export const HINT = "text-xs leading-relaxed text-muted-foreground";

export const TABLE_HEAD =
  "border-b border-border/70 bg-muted/30 text-left text-[11px] font-semibold uppercase tracking-[0.06em] text-muted-foreground";

export const TABLE_ROW =
  "border-b border-border/50 last:border-0 transition-colors hover:bg-muted/40";

export const SCROLL_X = "overflow-x-auto overscroll-x-contain";
