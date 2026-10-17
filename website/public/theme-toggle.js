/* The header's light/dark switch. Remembers the choice in this browser only (no cookie). */
(function () {
  function sync(button) {
    var dark = document.documentElement.classList.contains("dark");
    button.setAttribute("aria-pressed", dark ? "true" : "false");
    button.setAttribute("aria-label", dark ? "Use the light theme" : "Use the dark theme");
  }
  document.querySelectorAll("[data-theme-toggle]").forEach(function (button) {
    sync(button);
    button.addEventListener("click", function () {
      var dark = !document.documentElement.classList.contains("dark");
      document.documentElement.classList.toggle("dark", dark);
      try {
        window.localStorage.setItem("servabo-site-theme", dark ? "dark" : "light");
      } catch (error) {
        /* storage blocked: the choice lasts until the page closes */
      }
      document.querySelectorAll("[data-theme-toggle]").forEach(sync);
    });
  });
})();
