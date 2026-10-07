/*
 * Applies the saved theme before the app's code loads, so a dark-mode page is never painted
 * light first. Mirrors applyTheme() in src/store/useUIStore.ts (same storage key and rules).
 * A same-origin file because the Content-Security-Policy allows no inline script.
 */
(function () {
  try {
    var saved = JSON.parse(window.localStorage.getItem("flowpilot_ui_preferences") || "{}");
    var theme = (saved && saved.state && saved.state.theme) || "system";
    var dark =
      theme === "dark" ||
      (theme === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
    document.documentElement.classList.toggle("dark", dark);
  } catch (error) {
    /* Storage blocked: the app applies the theme once it loads. */
  }
})();
