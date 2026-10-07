/**
 * Class strings shared by the sign-in, sign-up, password and verification
 * pages, so every pre-auth form looks and behaves the same.
 */

export const AUTH_LABEL = "block text-[13px] font-medium text-foreground";

/** Inputs with a leading icon add `pl-9`; with a trailing button add `pr-10`. */
export const AUTH_INPUT =
  "fp-input h-10 px-3 text-sm disabled:cursor-not-allowed disabled:opacity-60 aria-[invalid=true]:border-destructive/70 aria-[invalid=true]:focus-visible:border-destructive";

export const AUTH_INPUT_ICON = "pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground/80";

export const AUTH_TRAILING_BUTTON =
  "absolute right-1.5 top-1/2 flex h-7 w-7 -translate-y-1/2 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50";

export const AUTH_PRIMARY = "fp-btn fp-btn-primary h-10 w-full text-sm font-semibold";

export const AUTH_SECONDARY = "fp-btn fp-btn-secondary h-10 w-full text-sm";

export const AUTH_LINK =
  "font-medium text-primary underline-offset-4 hover:underline dark:text-[hsl(213_94%_72%)]";

export const AUTH_MUTED_LINK =
  "text-[13px] text-muted-foreground underline-offset-4 hover:text-foreground hover:underline disabled:opacity-50";

export const AUTH_TITLE = "text-[22px] font-semibold leading-tight tracking-tight text-foreground";

export const AUTH_SUBTITLE = "text-sm leading-relaxed text-muted-foreground";

export const AUTH_ERROR =
  "flex items-start gap-2 rounded-lg border border-destructive/30 bg-destructive/[0.08] px-3 py-2.5 text-[13px] text-destructive animate-fade-in";

export const AUTH_FIELD_ERROR = "text-xs font-medium text-destructive";
