// Global page loading overlay (see .app-loader in design-system.css).
// Shows the loader while the page loads, then fades it out once the
// first paint has landed (DOMContentLoaded + two animation frames),
// not on window load — late images and fonts must not hold the page
// covered. A 6s failsafe guarantees the overlay can never trap the
// page. window.appLoader.show()/hide() are available for manual
// control.
//
// The same overlay also covers an internal navigation link click,
// so moving between pages feels continuous — but only after a
// 150ms timer: a navigation that commits faster than that never
// flashes the overlay at all. The click is never intercepted — no
// artificial delay to the navigation itself; the browser navigates
// exactly as it normally would, and the incoming page's own
// first-paint handling fades the overlay out.
//
// Click covers are INSTANT and near-opaque (app-loading-instant):
// a slow translucent fade-in over the old page felt like briefly
// going back to it, so the click path skips the fade entirely and
// hides the old page at once. The incoming page is a fresh
// document, so its normal 0.45s fade-out is untouched.
//
// A click cover has NO time limit: the old document stays alive
// until the new document commits, so a 6s failsafe firing on the
// old document mid-navigation would strip the cover and reveal
// the clear old page again. Instead, a click cover only goes away
// when the document unloads (navigation commits) or the user
// explicitly dismisses it (Escape, or clicking the overlay). The
// load-time overlay keeps its 6s failsafe unchanged.
(function () {
  var root = document.documentElement;
  var failsafeId = null;
  var clickCover = false;
  var clickTimerId = null;
  function clearClickTimer() {
    if (clickTimerId !== null) clearTimeout(clickTimerId);
    clickTimerId = null;
  }
  function armFailsafe() {
    if (failsafeId !== null) clearTimeout(failsafeId);
    failsafeId = setTimeout(hide, 6000);
  }
  function clearFailsafe() {
    if (failsafeId !== null) clearTimeout(failsafeId);
    failsafeId = null;
  }
  function hide() {
    // Navigation pending: never strip the click cover, not even
    // on the load event or the load failsafe.
    if (clickCover) return;
    root.classList.remove("app-loading");
    root.classList.remove("app-loading-instant");
  }
  function dismissClickCover() {
    if (!clickCover) return;
    clickCover = false;
    clearFailsafe();
    clearClickTimer();
    root.classList.remove("app-loading");
    root.classList.remove("app-loading-instant");
  }
  window.appLoader = {
    show: function (instant) {
      root.classList.add("app-loading");
      if (instant) {
        clickCover = true;
        root.classList.add("app-loading-instant");
        clearFailsafe();
      } else {
        armFailsafe();
      }
    },
    hide: hide,
  };
  root.classList.add("app-loading");
  armFailsafe();
  // Lift the overlay once the first paint has landed: the second
  // animation frame runs after the browser has painted the frame
  // queued by the first, so content is visible underneath. Waiting
  // for window load instead held the overlay over every late image
  // and font. If parsing already finished, run the frame chain now.
  function hideAfterFirstPaint() {
    requestAnimationFrame(function () {
      requestAnimationFrame(hide);
    });
  }
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", hideAfterFirstPaint);
  } else {
    hideAfterFirstPaint();
  }
  // A page restored from the back/forward cache keeps its old DOM —
  // never let it come back with the overlay stuck on. A restore is
  // NOT a pending navigation, so the click-cover flag must be
  // dropped first: hide() alone is blocked while the flag is set,
  // which would leave the restored page covered until Escape or
  // an overlay click.
  window.addEventListener("pageshow", function (event) {
    if (event.persisted) {
      clickCover = false;
      hide();
    }
  });
  // A navigation that commits before the click timer fires must
  // never flash the cover in the outgoing document's last moments.
  window.addEventListener("pagehide", clearClickTimer);
  // Escape hatch for a hung navigation: Escape or a click on the
  // overlay dismisses a click cover. The load-time overlay is
  // never user-dismissable; only a click cover is.
  document.addEventListener("keydown", function (event) {
    if (clickCover && event.key === "Escape") dismissClickCover();
  });
  var overlay = document.querySelector(".app-loader");
  if (overlay) {
    overlay.addEventListener("click", function () {
      if (clickCover) dismissClickCover();
    });
  }
  document.addEventListener("click", function (event) {
    if (event.defaultPrevented) {
      // This click's navigation was cancelled by another handler:
      // drop any cover still waiting on its timer instead of
      // flashing it over a page that is going nowhere.
      clearClickTimer();
      return;
    }
    if (event.button !== 0) return;
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
    // Delayed instant cover: fast navigations commit before the
    // timer fires and never flash; slower ones still get the
    // instant, fade-free cover, just 150ms in. Re-arming on each
    // qualifying click keeps at most one timer pending.
    clearClickTimer();
    clickTimerId = setTimeout(function () {
      clickTimerId = null;
      window.appLoader.show(true);
    }, 150);
  });
})();
