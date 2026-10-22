// Global page loading overlay (see .app-loader in design-system.css).
// Shows the frosted-glass loader while the page loads, then fades it
// out. A 6s failsafe guarantees the overlay can never trap the page.
// window.appLoader.show()/hide() are available for manual control.
//
// The same overlay also appears the moment an internal navigation
// link is clicked, so moving between pages feels continuous. The
// click is never intercepted — no preventDefault, no artificial
// delay; the browser navigates exactly as it normally would, and
// the incoming page's own load handling fades the overlay out.
(function () {
  var root = document.documentElement;
  var failsafeId = null;
  function armFailsafe() {
    if (failsafeId !== null) clearTimeout(failsafeId);
    failsafeId = setTimeout(hide, 6000);
  }
  function hide() { root.classList.remove("app-loading"); }
  window.appLoader = {
    show: function () { root.classList.add("app-loading"); armFailsafe(); },
    hide: hide,
  };
  root.classList.add("app-loading");
  armFailsafe();
  if (document.readyState === "complete") {
    hide();
  } else {
    window.addEventListener("load", hide);
  }
  // A page restored from the back/forward cache keeps its old DOM —
  // never let it come back with the overlay stuck on.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) hide();
  });
  document.addEventListener("click", function (event) {
    if (event.defaultPrevented || event.button !== 0) return;
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    var target = event.target;
    var link = target && target.closest ? target.closest("a[href]") : null;
    if (!link) return;
    if (link.target && link.target !== "_self") return;
    if (link.hasAttribute("download")) return;
    var href = link.getAttribute("href");
    if (!href || href.charAt(0) === "#") return;
    var url;
    try { url = new URL(link.href); } catch (err) { return; }
    if (url.origin !== window.location.origin) return;
    if (url.protocol !== "http:" && url.protocol !== "https:") return;
    // API links are actions/downloads, not page changes.
    if (url.pathname.indexOf("/api/") === 0) return;
    // Same page (only the hash differs): no unload is coming.
    if (url.pathname === window.location.pathname && url.search === window.location.search) return;
    window.appLoader.show();
  });
})();
