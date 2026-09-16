/**
 * ARCH36-S1:command-palette-event — how anything opens the command palette.
 *
 * A window event rather than a store field, so the sidebar's search button
 * does not import the palette (and its list rendering) just to open it, and
 * the palette stays the only owner of its own open state.
 */
export const OPEN_COMMAND_PALETTE_EVENT = "flowpilot:open-command-palette";

export const openCommandPalette = (): void => {
  window.dispatchEvent(new Event(OPEN_COMMAND_PALETTE_EVENT));
};

/** "⌘K" on Apple platforms, "Ctrl K" elsewhere. Display only. */
export const commandPaletteShortcutLabel = (): string => {
  const platform =
    typeof navigator === "undefined" ? "" : navigator.platform || navigator.userAgent;
  return /mac|iphone|ipad/i.test(platform) ? "⌘K" : "Ctrl K";
};
