// Offline banner behaviour (read-only offline v1).
//
// The banner element lives in both layouts. It appears whenever the
// browser reports offline and labels any saved data with the newest
// cached-at time the service worker reports — cached data is never
// presented as fresh (project rule).
(function () {
  "use strict";

  const banner = document.getElementById("offlineBanner");
  if (!banner) return;
  const timeElement = document.getElementById("offlineBannerTime");

  function askDataStatus() {
    return new Promise((resolve) => {
      if (!("serviceWorker" in navigator) || !navigator.serviceWorker.controller) {
        resolve(null);
        return;
      }
      const channel = new MessageChannel();
      const timer = setTimeout(() => resolve(null), 800);
      channel.port1.onmessage = (event) => {
        clearTimeout(timer);
        resolve(event.data || null);
      };
      try {
        navigator.serviceWorker.controller.postMessage(
          { type: "GET_DATA_STATUS" },
          [channel.port2],
        );
      } catch (error) {
        clearTimeout(timer);
        resolve(null);
      }
    });
  }

  // The worker also tells us when it serves saved content — some
  // browsers keep navigator.onLine true while the network is down.
  let servedFromCache = false;
  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("message", (event) => {
      if (event.data && event.data.type === "SERVED_FROM_CACHE") {
        servedFromCache = true;
        refresh();
      }
    });
  }

  async function refresh() {
    if (navigator.onLine && !servedFromCache) {
      banner.hidden = true;
      return;
    }
    banner.hidden = false;
    if (!timeElement) return;
    const status = await askDataStatus();
    if (status && status.latest) {
      const when = new Date(status.latest);
      timeElement.textContent =
        "Showing saved data from " +
        when.toLocaleString([], {
          day: "numeric",
          month: "short",
          hour: "numeric",
          minute: "2-digit",
        }) +
        ".";
    } else {
      timeElement.textContent = "No saved data on this device yet.";
    }
  }

  window.addEventListener("online", () => {
    servedFromCache = false;
    refresh();
  });
  window.addEventListener("offline", refresh);
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) refresh();
  });
  refresh();
})();
