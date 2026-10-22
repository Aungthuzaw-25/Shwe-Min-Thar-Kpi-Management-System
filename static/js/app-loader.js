// Global page loading overlay (see .app-loader in design-system.css).
// Shows the frosted-glass loader while the page loads, then fades it
// out. A 6s failsafe guarantees the overlay can never trap the page.
// window.appLoader.show()/hide() are available for manual control.
(function () {
  var root = document.documentElement;
  function hide() { root.classList.remove("app-loading"); }
  window.appLoader = {
    show: function () { root.classList.add("app-loading"); },
    hide: hide,
  };
  root.classList.add("app-loading");
  if (document.readyState === "complete") {
    hide();
  } else {
    window.addEventListener("load", hide);
  }
  setTimeout(hide, 6000);
})();
