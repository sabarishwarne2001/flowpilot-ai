/* The Helm page's switch demo: it loops only while it is on screen, starting a moment after load (and never under reduced motion,
   where the CSS shows Helm still). A file, not an inline script, under this site's CSP. */
(function () {
  var demos = document.querySelectorAll("[data-switch-demo]");
  if (!demos.length || !("IntersectionObserver" in window)) return;
  var observer = new IntersectionObserver(
    function (entries) {
      entries.forEach(function (entry) {
        entry.target.classList.toggle("is-playing", entry.isIntersecting);
      });
    },
    { threshold: 0.35 }
  );
  // Start once the page has finished loading and settled, so the loop never slows the first view.
  function start() {
    window.setTimeout(function () {
      demos.forEach(function (demo) {
        observer.observe(demo);
      });
    }, 2500);
  }
  if (document.readyState === "complete") start();
  else window.addEventListener("load", start, { once: true });
})();
