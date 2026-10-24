import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import request from "supertest";
import { createApp } from "../src/server.js";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const workerSource = fs.readFileSync(
  path.join(repoRoot, "web/src/service-worker.js"),
  "utf8",
);

describe("offline routes", () => {
  it("serves the service worker at the root with no-cache", async () => {
    const response = await request(createApp()).get("/sw.js");
    expect(response.status).toBe(200);
    expect(response.headers["content-type"]).toContain("javascript");
    expect(response.headers["cache-control"]).toContain("no-cache");
    expect(response.text).toContain("kpi-shell-");
    expect(response.text).toBe(workerSource);
  });

  it("serves the offline fallback page without a session", async () => {
    const response = await request(createApp()).get("/offline");
    expect(response.status).toBe(200);
    expect(response.headers["content-type"]).toContain("html");
    expect(response.text).toContain("You are offline");
  });
});

describe("service worker safety rules", () => {
  it("caches only the allowlisted dashboard reads", () => {
    const allowlistMatch = workerSource.match(
      /const DATA_ALLOWLIST = \[([\s\S]*?)\];/,
    );
    expect(allowlistMatch).not.toBeNull();
    const allowlist = allowlistMatch[1];
    expect(allowlist).toContain("/api/v1/dashboard/summary");
    expect(allowlist).toContain("/api/v1/dashboard/executive/overview");
    expect(allowlist).toContain("/api/v1/dashboard/executive/employees");
    for (const forbidden of ["auth", "permissions", "users", "presence", "settings"]) {
      expect(allowlist).not.toContain(forbidden);
    }
  });

  it("never intercepts non-GET requests", () => {
    expect(workerSource).toContain('request.method !== "GET"');
  });

  it("supports owner sealing and private-cache purge messages", () => {
    expect(workerSource).toContain('"SET_OWNER"');
    expect(workerSource).toContain('"PURGE_PRIVATE"');
    expect(workerSource).toContain('"GET_DATA_STATUS"');
    expect(workerSource).toContain('"SERVED_FROM_CACHE"');
  });

  it("precaches the real shell assets, not the Tailwind source", () => {
    expect(workerSource).toContain("/static/css/output.css");
    expect(workerSource).toContain("/static/css/liquid-glass-theme.css?v=20261021-4");
    expect(workerSource).not.toContain("input.css");
    expect(workerSource).toContain("/offline");
  });
});

describe("offline wiring in templates and page scripts", () => {
  const base = fs.readFileSync(path.join(repoRoot, "templates/layouts/base.njk"), "utf8");
  const employee = fs.readFileSync(
    path.join(repoRoot, "templates/layouts/employee_base.njk"),
    "utf8",
  );
  const login = fs.readFileSync(path.join(repoRoot, "templates/auth/login.njk"), "utf8");

  it.each([
    ["admin layout", base],
    ["employee layout", employee],
  ])("%s carries the banner, the scripts and the logout purge", (_name, layout) => {
    expect(layout).toContain('id="offlineBanner"');
    expect(layout).toContain('data-user-id="{{ user_id }}"');
    expect(layout).toContain("/static/js/sw-register.js");
    expect(layout).toContain("/static/js/offline-ui.js");
    expect(layout).toContain("kpiPurgeOfflineCaches");
  });

  it("login page purges private caches and does not register the worker", () => {
    expect(login).toContain("PURGE_PRIVATE");
    expect(login).not.toContain("sw-register.js");
    expect(login).not.toContain("serviceWorker.register");
  });

  it("page scripts expose the purge hook and ask the worker for data status", () => {
    const register = fs.readFileSync(
      path.join(repoRoot, "static/js/sw-register.js"),
      "utf8",
    );
    const banner = fs.readFileSync(
      path.join(repoRoot, "static/js/offline-ui.js"),
      "utf8",
    );
    expect(register).toContain("window.kpiPurgeOfflineCaches");
    expect(register).toContain('register("/sw.js")');
    expect(banner).toContain("GET_DATA_STATUS");
    expect(banner).toContain("offlineBannerTime");
  });
});

describe("service worker page owner check (behaviour)", () => {
  const ORIGIN = "https://app.test";
  const OFFLINE_TEXT = "GENERIC OFFLINE PAGE";

  class FakeCache {
    constructor(networkFetch) {
      this.store = new Map();
      this.networkFetch = networkFetch;
    }
    keyOf(request, ignoreSearch = false) {
      const raw = typeof request === "string" ? request : request.url;
      const url = new URL(raw, ORIGIN);
      if (ignoreSearch) url.search = "";
      return url.href;
    }
    async put(request, response) {
      const body = await response.text();
      this.store.set(this.keyOf(request), {
        body,
        status: response.status,
        headers: Object.fromEntries(response.headers.entries()),
      });
    }
    async match(request, options = {}) {
      const entry = this.store.get(this.keyOf(request, options.ignoreSearch));
      if (!entry) return undefined;
      return new Response(entry.body, { status: entry.status, headers: entry.headers });
    }
    async add(request) {
      const response = await this.networkFetch(request);
      if (!response.ok) throw new Error("cache.add failed");
      await this.put(request, response);
    }
    async keys() {
      return [...this.store.keys()].map((url) => ({ url }));
    }
    async delete(request) {
      return this.store.delete(this.keyOf(request));
    }
  }

  function loadWorker() {
    const vm = require("node:vm");
    const network = { up: true, pageBody: "PAGE FOR A" };
    const networkFetch = async () => {
      if (!network.up) throw new TypeError("network down");
      return new Response(network.pageBody, {
        status: 200,
        headers: { "content-type": "text/html" },
      });
    };
    const cacheStores = new Map();
    const caches = {
      async open(name) {
        if (!cacheStores.has(name)) cacheStores.set(name, new FakeCache(networkFetch));
        return cacheStores.get(name);
      },
      async delete(name) {
        return cacheStores.delete(name);
      },
      async keys() {
        return [...cacheStores.keys()];
      },
    };
    const listeners = {};
    const sandbox = {
      caches,
      fetch: networkFetch,
      location: { origin: ORIGIN },
      clients: { matchAll: async () => [], claim: async () => {} },
      skipWaiting: async () => {},
      addEventListener: (type, fn) => {
        listeners[type] = fn;
      },
      Request,
      Response,
      Headers,
      URL,
      console,
    };
    sandbox.self = sandbox;
    vm.createContext(sandbox);
    vm.runInContext(workerSource, sandbox);
    return { listeners, caches, network };
  }

  async function seedOfflinePage(caches) {
    const shell = await caches.open("kpi-shell-v1");
    await shell.put("/offline", new Response(OFFLINE_TEXT, { status: 200 }));
  }

  async function sendOwner(listeners, owner) {
    const event = { data: { type: "SET_OWNER", owner }, waitUntil(p) { this.done = p; } };
    listeners.message(event);
    await event.done;
  }

  async function navigate(listeners, path) {
    const request = { method: "GET", url: `${ORIGIN}${path}`, mode: "navigate" };
    const event = { request, respondWith(p) { this.response = p; } };
    listeners.fetch(event);
    return event.response;
  }

  it("serves the cached page offline when the owner matches", async () => {
    const { listeners, caches, network } = loadWorker();
    await seedOfflinePage(caches);
    await sendOwner(listeners, "hashA");

    const online = await navigate(listeners, "/dashboard");
    expect(await online.text()).toBe("PAGE FOR A");

    network.up = false;
    const offline = await navigate(listeners, "/dashboard");
    expect(await offline.text()).toBe("PAGE FOR A");
  });

  it("serves the generic offline page when the page owner differs", async () => {
    const { listeners, caches, network } = loadWorker();
    await seedOfflinePage(caches);
    // Page cached for A, but the worker's stored owner is B (state the
    // read path must defend against without relying on purge events).
    const pages = await caches.open("kpi-pages-v1");
    await pages.put(`${ORIGIN}/dashboard`, new Response("PAGE FOR A", { status: 200 }));
    const meta = await caches.open("kpi-meta-v1");
    await meta.put("/dashboard::page-meta", new Response(JSON.stringify({ ts: 1, owner: "hashA" }), { headers: { "content-type": "application/json" } }));
    await meta.put("/__kpi_meta__/owner", new Response("hashB", { headers: { "content-type": "text/plain" } }));

    network.up = false;
    const response = await navigate(listeners, "/dashboard");
    expect(await response.text()).toBe(OFFLINE_TEXT);
  });

  it("serves the generic offline page when no owner is known", async () => {
    const { listeners, caches, network } = loadWorker();
    await seedOfflinePage(caches);
    const pages = await caches.open("kpi-pages-v1");
    await pages.put(`${ORIGIN}/dashboard`, new Response("PAGE FOR A", { status: 200 }));
    const meta = await caches.open("kpi-meta-v1");
    await meta.put("/dashboard::page-meta", new Response(JSON.stringify({ ts: 1, owner: "hashA" }), { headers: { "content-type": "application/json" } }));
    // No stored owner at all — e.g. the browser reopened before any
    // sign-in check could confirm who is viewing.
    network.up = false;
    const response = await navigate(listeners, "/dashboard");
    expect(await response.text()).toBe(OFFLINE_TEXT);
  });

  it("does not serve a page that has no owner tag (cached before tagging existed)", async () => {
    const { listeners, caches, network } = loadWorker();
    await seedOfflinePage(caches);
    await sendOwner(listeners, "hashA");
    const pages = await caches.open("kpi-pages-v1");
    await pages.put(`${ORIGIN}/dashboard`, new Response("LEGACY PAGE", { status: 200 }));

    network.up = false;
    const response = await navigate(listeners, "/dashboard");
    expect(await response.text()).toBe(OFFLINE_TEXT);
  });
});
