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
