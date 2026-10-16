/*
 * The visitor's theme before first paint: their saved choice, else their system's. A file, not an
 * inline script, so the Content-Security-Policy can forbid inline scripts entirely.
 */
(function () {
  try {
    var saved = window.localStorage.getItem("servabo-site-theme");
    var dark = saved ? saved === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
    document.documentElement.classList.toggle("dark", dark);
  } catch (error) {
    if (window.matchMedia("(prefers-color-scheme: dark)").matches) document.documentElement.classList.add("dark");
  }
})();
