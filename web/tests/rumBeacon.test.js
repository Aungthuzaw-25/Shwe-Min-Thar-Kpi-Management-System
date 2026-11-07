// RUM beacon unit tests (web-perf-gaps item 1): the route sanitizer
// and the payload builder are pure functions inside the beacon's
// IIFE, so they are evaluated here in a vm sandbox (the browser
// entry point below the marker is not evaluated).
import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import { runInNewContext } from "node:vm";

const MARKER = "// --- browser entry point";

function loadBeacon() {
  const source = readFileSync(
    new URL("../../static/js/rum-beacon.js", import.meta.url),
    "utf-8",
  );
  const head = source.split(MARKER)[0];
  // Strip the IIFE wrapper so the pure functions land on the sandbox.
  const body = head
    .replace(/^\(function \(\) \{\s*$/m, "")
    .replace(/^\s*"use strict";\s*$/m, "");
  const sandbox = {};
  runInNewContext(body, sandbox);
  return sandbox;
}

const beacon = loadBeacon();

describe("rum beacon route sanitizer", () => {
  it("keeps plain route templates verbatim", () => {
    expect(beacon.sanitizeRoute("/superadmin/dashboard")).toBe(
      "/superadmin/dashboard",
    );
    expect(beacon.sanitizeRoute("/auth/login")).toBe("/auth/login");
    expect(beacon.sanitizeRoute("/")).toBe("/");
  });

  it("replaces numeric, UUID and long-hex segments with id", () => {
    expect(beacon.sanitizeRoute("/superadmin/users/42/profile")).toBe(
      "/superadmin/users/id/profile",
    );
    expect(
      beacon.sanitizeRoute(
        "/superadmin/users/3fa85f64-5717-4562-b3fc-2c963f66afa6/profile",
      ),
    ).toBe("/superadmin/users/id/profile");
    expect(
      beacon.sanitizeRoute("/tasks/abcdef0123456789abcdef0123456789"),
    ).toBe("/tasks/id");
  });

  it("sends routes the endpoint accepts — bare id, no colon, no raw id", () => {
    // The endpoint's route pattern, copied from
    // app/modules/system/rum.py (branch feat/rum-endpoint, head
    // 0e4f94f): ^/[A-Za-z0-9/_-]{1,127}$. A colon in the route is a
    // 422, so id segments are replaced with the bare word "id" —
    // never ":id", never the raw id. If the endpoint pattern ever
    // changes, this test is where the mismatch shows up.
    const endpointRoute = /^\/[A-Za-z0-9/_-]{1,127}$/;
    const samples = [
      "/superadmin/dashboard",
      "/superadmin/users/id/profile",
      "/tasks/id",
      beacon.sanitizeRoute("/superadmin/users/42/profile"),
      beacon.sanitizeRoute(
        "/superadmin/users/3fa85f64-5717-4562-b3fc-2c963f66afa6/profile",
      ),
      beacon.sanitizeRoute("/tasks/abcdef0123456789abcdef0123456789"),
      beacon.sanitizeRoute("/" + "x".repeat(200)),
    ];
    for (const route of samples) {
      expect(route).toMatch(endpointRoute);
      expect(route).not.toContain(":");
    }
    // A real UUID segment is replaced, never sent raw.
    expect(
      beacon.sanitizeRoute(
        "/superadmin/users/3fa85f64-5717-4562-b3fc-2c963f66afa6/profile",
      ),
    ).not.toContain("3fa85f64-5717-4562-b3fc-2c963f66afa6");
    // A numeric segment is replaced, never sent raw.
    expect(beacon.sanitizeRoute("/superadmin/users/42/profile")).not.toContain(
      "/42/",
    );
  });

  it("never sends a query string or fragment", () => {
    expect(beacon.sanitizeRoute("/superadmin/kpi?formula=abc&tab=x")).toBe(
      "/superadmin/kpi",
    );
    expect(beacon.sanitizeRoute("/dashboard#section")).toBe("/dashboard");
  });

  it("caps the route at 128 chars", () => {
    // "x" is not a hex digit, so the long segment is kept as a route
    // part (not an id) and the cap is what truncates it.
    const long = "/" + "x".repeat(200);
    expect(beacon.sanitizeRoute(long)).toHaveLength(128);
  });
});

describe("rum beacon payload builder", () => {
  it("keeps only the endpoint's fields, rounded and range-checked", () => {
    const payload = beacon.buildPayload({
      route: "/superadmin/users/7/profile",
      ttfb_ms: 123.4,
      dom_content_loaded_ms: 456,
      load_ms: 789,
      first_paint_ms: 100,
      first_contentful_paint_ms: 200,
      largest_contentful_paint_ms: 345.6,
      cls: 0.02,
      connection: "4g",
      navigation_type: "navigate",
    });
    expect(payload).toEqual({
      route: "/superadmin/users/id/profile",
      ttfb_ms: 123,
      dom_content_loaded_ms: 456,
      load_ms: 789,
      first_paint_ms: 100,
      first_contentful_paint_ms: 200,
      largest_contentful_paint_ms: 346,
      cls: 0.02,
      connection: "4g",
      navigation_type: "navigate",
    });
  });

  it("omits out-of-range values and unknown fields instead of sending them", () => {
    const payload = beacon.buildPayload({
      route: "/dashboard",
      ttfb_ms: 700000,
      dom_content_loaded_ms: -5,
      largest_contentful_paint_ms: NaN,
      cls: 1.5,
      connection: "5g",
      navigation_type: "teleport",
      user_id: 42,
      email: "sa@example.com",
    });
    expect(payload).toEqual({ route: "/dashboard" });
  });

  it("sends no PII-shaped keys even when the input carries them", () => {
    const payload = beacon.buildPayload({
      route: "/dashboard",
      userId: 1,
      user_id: 1,
      ip: "127.0.0.1",
    });
    expect(Object.keys(payload)).toEqual(["route"]);
  });
});

describe("rum beacon wiring", () => {
  it("is loaded (defer) on both layouts and the login page", async () => {
    const { readFileSync } = await import("node:fs");
    const tag = '<script src="/static/js/rum-beacon.js" defer></script>';
    for (const file of [
      "templates/layouts/base.njk",
      "templates/layouts/employee_base.njk",
      "templates/auth/login.njk",
    ]) {
      expect(readFileSync(new URL(`../../${file}`, import.meta.url), "utf-8")).toContain(
        tag,
      );
    }
  });
});
