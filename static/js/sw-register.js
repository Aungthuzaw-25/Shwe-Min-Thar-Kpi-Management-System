// Service worker registration + cache ownership (read-only offline v1).
//
// Registers the root-scoped worker served at /sw.js, then tells it
// which user is signed in (as a SHA-256 hash of the user id — the raw
// id never leaves the page). The worker seals cached pages/data to
// that owner: a different owner, a logout, or the login page purges
// them. Also exposes window.kpiPurgeOfflineCaches() for handleLogout().
(function () {
  "use strict";

  async function hashOwner(raw) {
    try {
      const digest = await crypto.subtle.digest(
        "SHA-256",
        new TextEncoder().encode(String(raw)),
      );
      return Array.from(new Uint8Array(digest))
        .map((byte) => byte.toString(16).padStart(2, "0"))
        .join("");
    } catch (error) {
      return "";
    }
  }

  async function purgePrivateCaches() {
    try {
      if ("caches" in window) {
        const names = await caches.keys();
        await Promise.all(
          names
            .filter((name) => name.startsWith("kpi-") && !name.startsWith("kpi-shell-"))
            .map((name) => caches.delete(name)),
        );
      }
    } catch (error) {
      // Purging must never break logout — the login page purges again.
    }
    try {
      if ("serviceWorker" in navigator) {
        const registration = await navigator.serviceWorker.getRegistration();
        const targets = [
          navigator.serviceWorker.controller,
          registration && registration.active,
          registration && registration.waiting,
        ];
        targets.forEach((target) => {
          if (target) target.postMessage({ type: "PURGE_PRIVATE" });
        });
      }
    } catch (error) {
      // Same reasoning as above.
    }
  }

  window.kpiPurgeOfflineCaches = purgePrivateCaches;

  if (!("serviceWorker" in navigator)) return;

  window.addEventListener("load", async () => {
    try {
      const registration = await navigator.serviceWorker.register("/sw.js");
      const banner = document.getElementById("offlineBanner");
      const rawId = banner ? banner.getAttribute("data-user-id") || "" : "";
      const owner = rawId ? await hashOwner(rawId) : "";
      if (!owner) return;
      const sendOwner = () => {
        const target = registration.active || navigator.serviceWorker.controller;
        if (target) target.postMessage({ type: "SET_OWNER", owner });
      };
      sendOwner();
      navigator.serviceWorker.addEventListener("controllerchange", sendOwner);
      navigator.serviceWorker.ready.then(sendOwner).catch(() => {});
    } catch (error) {
      // Offline support is a progressive enhancement; the app works
      // exactly as before when registration is unavailable.
    }
  });
})();
