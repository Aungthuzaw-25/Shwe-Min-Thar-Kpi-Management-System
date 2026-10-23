// Shwe Min Thar KPI — service worker (read-only offline, v1).
//
// Served by the web tier at GET /sw.js so its scope is the whole origin
// ("/"). The previous workers lived under /static/ and could never
// control an app page; this one can. Design rules (see
// agent-docs/frontend/Real-Offline-Plan.md):
//
//   * Read-only: only GET requests are handled; writes are never
//     intercepted, queued or replayed.
//   * Only three API reads are cached (DATA_ALLOWLIST). Auth,
//     permissions, users, presence, settings and every other API path
//     are network-only and never touch a cache.
//   * Cached data/pages belong to one signed-in user ("owner", a hash
//     of the user id sent by sw-register.js). A different owner purges
//     them; logout and the login page purge them too.
//   * The offline banner in the layouts labels cached data with its
//     cached-at time — stale data is never presented as fresh.
//   * Bump CACHE_VERSION on any deploy that changes shell assets whose
//     URL is not already versioned; activate deletes older kpi-* caches.

const CACHE_VERSION = "v1";
const SHELL_CACHE = `kpi-shell-${CACHE_VERSION}`;
const PAGES_CACHE = `kpi-pages-${CACHE_VERSION}`;
const DATA_CACHE = `kpi-data-${CACHE_VERSION}`;
const META_CACHE = `kpi-meta-${CACHE_VERSION}`;
const PRIVATE_CACHES = [PAGES_CACHE, DATA_CACHE, META_CACHE];
const ALL_CACHES = [SHELL_CACHE, ...PRIVATE_CACHES];

const OFFLINE_URL = "/offline";

// The assets the two layouts actually load (NOT the Tailwind source).
// Versioned URLs must match the layout markup byte-for-byte or the
// cache lookup misses. Individual failures are tolerated at install:
// a missing asset simply gets filled in at runtime on first use.
const SHELL_URLS = [
  OFFLINE_URL,
  "/static/manifest.json",
  "/static/image/smt-logo.jpg",
  "/static/css/output.css",
  "/static/css/design-system.css",
  "/static/css/design-system.css?v=20261015-1",
  "/static/css/enterprise-ui.css",
  "/static/css/executive-dashboard.css",
  "/static/css/liquid-glass-theme.css?v=20261021-4",
  "/static/css/notifications.css",
  "/static/css/dashboard.css",
  "/static/vendor/chart.umd.min.js",
  "/static/js/api-fetch.js",
  "/static/js/app-loader.js",
  "/static/js/csp-event-bridge.js",
  "/static/js/csrf.js",
  "/static/js/notifications.js",
  "/static/js/offline-ui.js",
  "/static/js/sw-register.js",
];

// The ONLY API responses that may be cached (read-only dashboard data).
const DATA_ALLOWLIST = [
  "/api/v1/dashboard/summary",
  "/api/v1/dashboard/executive/overview",
  "/api/v1/dashboard/executive/employees",
];

const OWNER_META_URL = "/__kpi_meta__/owner";

let currentOwner = null;

function isAllowlistedData(url) {
  if (!url.pathname.startsWith("/api/v1/dashboard/")) return false;
  return DATA_ALLOWLIST.some(
    (prefix) => url.pathname === prefix || url.pathname.startsWith(`${prefix}/`),
  );
}

async function readStoredOwner() {
  try {
    const meta = await caches.open(META_CACHE);
    const hit = await meta.match(OWNER_META_URL);
    if (hit) return (await hit.text()) || null;
  } catch {
    // Cache unavailable — treat as no owner.
  }
  return null;
}

async function writeStoredOwner(owner) {
  const meta = await caches.open(META_CACHE);
  await meta.put(
    OWNER_META_URL,
    new Response(owner || "", { headers: { "content-type": "text/plain" } }),
  );
}

async function purgePrivateCaches() {
  await Promise.all(PRIVATE_CACHES.map((name) => caches.delete(name)));
}

// Tell open pages that content was just served from cache, so the
// offline banner can label it (navigator.onLine alone is not a
// reliable signal in every browser).
async function notifyServedFromCache(ts) {
  try {
    const windows = await self.clients.matchAll({ type: "window" });
    windows.forEach((client) =>
      client.postMessage({ type: "SERVED_FROM_CACHE", ts: ts || null }),
    );
  } catch {
    // No clients to notify — nothing to label.
  }
}

async function ensureOwnerLoaded() {
  if (currentOwner !== null) return currentOwner;
  currentOwner = (await readStoredOwner()) || "";
  return currentOwner;
}

// --- lifecycle -----------------------------------------------------------

self.addEventListener("install", (event) => {
  event.waitUntil(
    (async () => {
      const shell = await caches.open(SHELL_CACHE);
      // Settle individually: one missing asset must not kill offline.
      await Promise.allSettled(
        SHELL_URLS.map((url) => shell.add(new Request(url, { credentials: "same-origin" }))),
      );
      await self.skipWaiting();
    })(),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(
        names
          .filter((name) => name.startsWith("kpi-") && !ALL_CACHES.includes(name))
          .map((name) => caches.delete(name)),
      );
      await self.clients.claim();
    })(),
  );
});

// --- messages from pages (sw-register.js / offline-ui.js) ----------------

self.addEventListener("message", (event) => {
  const data = event.data || {};
  if (data.type === "SET_OWNER" && typeof data.owner === "string") {
    event.waitUntil(
      (async () => {
        const stored = await readStoredOwner();
        if (stored && data.owner && stored !== data.owner) {
          // A different user on this browser: their predecessor's
          // cached pages/data must never be served to them.
          await purgePrivateCaches();
        }
        currentOwner = data.owner;
        await writeStoredOwner(data.owner);
      })(),
    );
  }
  if (data.type === "PURGE_PRIVATE") {
    event.waitUntil(
      (async () => {
        currentOwner = "";
        await purgePrivateCaches();
      })(),
    );
  }
  if (data.type === "GET_DATA_STATUS" && event.ports && event.ports[0]) {
    const port = event.ports[0];
    event.waitUntil(
      (async () => {
        let latest = null;
        let count = 0;
        try {
          const meta = await caches.open(META_CACHE);
          const keys = await meta.keys();
          for (const request of keys) {
            if (!request.url.includes("::meta")) continue;
            const entry = await meta.match(request);
            if (!entry) continue;
            const parsed = await entry.json().catch(() => null);
            if (parsed && typeof parsed.ts === "number") {
              count += 1;
              if (latest === null || parsed.ts > latest) latest = parsed.ts;
            }
          }
        } catch {
          // Report "nothing saved" rather than failing the banner.
        }
        port.postMessage({ latest, count });
      })(),
    );
  }
});

// --- fetch strategies ------------------------------------------------------

async function networkFirstData(request, url) {
  const owner = await ensureOwnerLoaded();
  try {
    const response = await fetch(request);
    if (response.ok && owner) {
      const meta = await caches.open(META_CACHE);
      await meta.put(
        `${url.pathname}${url.search}::meta`,
        new Response(JSON.stringify({ ts: Date.now(), owner }), {
          headers: { "content-type": "application/json" },
        }),
      );
      const data = await caches.open(DATA_CACHE);
      await data.put(request, response.clone());
    }
    return response;
  } catch (error) {
    // Offline (or network down): serve this owner's cached copy, if any.
    const meta = await caches.open(META_CACHE);
    const metaHit = await meta.match(`${url.pathname}${url.search}::meta`);
    const parsed = metaHit ? await metaHit.json().catch(() => null) : null;
    if (parsed && owner && parsed.owner === owner) {
      const data = await caches.open(DATA_CACHE);
      const cached = await data.match(request);
      if (cached) {
        await notifyServedFromCache(parsed.ts);
        const body = await cached.text();
        const headers = new Headers(cached.headers);
        headers.set("x-kpi-offline-cache", "1");
        headers.set("x-kpi-cached-at", String(parsed.ts));
        return new Response(body, {
          status: cached.status,
          statusText: cached.statusText,
          headers,
        });
      }
    }
    throw error;
  }
}

async function navigationHandler(request) {
  try {
    const response = await fetch(request);
    const owner = await ensureOwnerLoaded();
    if (response.ok && !response.redirected && owner) {
      const pages = await caches.open(PAGES_CACHE);
      await pages.put(request, response.clone());
    }
    return response;
  } catch (error) {
    const pages = await caches.open(PAGES_CACHE);
    const cached = await pages.match(request, { ignoreSearch: true });
    if (cached) {
      await notifyServedFromCache(null);
      return cached;
    }
    const shell = await caches.open(SHELL_CACHE);
    const offline = await shell.match(OFFLINE_URL);
    if (offline) return offline;
    throw error;
  }
}

async function cacheFirstStatic(request) {
  const shell = await caches.open(SHELL_CACHE);
  const cached = await shell.match(request);
  if (cached) return cached;
  const response = await fetch(request);
  if (response.ok) {
    await shell.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return; // writes: never intercepted
  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;

  if (url.pathname.startsWith("/api/")) {
    if (isAllowlistedData(url)) {
      event.respondWith(networkFirstData(request, url));
    }
    return; // every other API path: network only, never cached
  }
  if (request.mode === "navigate") {
    event.respondWith(navigationHandler(request));
    return;
  }
  if (url.pathname.startsWith("/static/")) {
    event.respondWith(cacheFirstStatic(request));
  }
});
