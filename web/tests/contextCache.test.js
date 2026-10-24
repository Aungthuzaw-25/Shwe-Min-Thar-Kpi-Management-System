import { describe, expect, it, vi } from "vitest";
import { createBackendClient } from "../src/lib/backendClient.js";
import { loadPageContext } from "../src/lib/pageContext.js";

function jsonResponse(body, { ok = true, status = 200 } = {}) {
  return {
    ok,
    status,
    headers: { get: () => "application/json" },
    text: async () => JSON.stringify(body),
  };
}

function fakeRequest(cookie = "session=abc") {
  return { get: (name) => (name === "cookie" ? cookie : undefined) };
}

describe("backend client context cache (Batch 2)", () => {
  it("reuses context GET answers for the same session", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ role: "Admin" }));
    const client = createBackendClient({ fetchImpl });
    const request = fakeRequest();

    const first = await client.get(request, "/api/v1/auth/me");
    const second = await client.get(request, "/api/v1/auth/me");

    expect(first).toEqual({ role: "Admin" });
    expect(second).toEqual({ role: "Admin" });
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("does not share cached answers across sessions", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ role: "Admin" }));
    const client = createBackendClient({ fetchImpl });

    await client.get(fakeRequest("session=one"), "/api/v1/auth/me");
    await client.get(fakeRequest("session=two"), "/api/v1/auth/me");

    expect(fetchImpl).toHaveBeenCalledTimes(2);
  });

  it("never caches non-context paths or error responses", async () => {
    const fetchImpl = vi.fn(async () => jsonResponse({ items: [] }));
    const client = createBackendClient({ fetchImpl });
    const request = fakeRequest();

    await client.get(request, "/api/v1/tasks");
    await client.get(request, "/api/v1/tasks");
    expect(fetchImpl).toHaveBeenCalledTimes(2);

    const failing = vi.fn()
      .mockResolvedValueOnce(jsonResponse({ detail: "nope" }, { ok: false, status: 500 }))
      .mockResolvedValueOnce(jsonResponse({ role: "Admin" }));
    const client2 = createBackendClient({ fetchImpl: failing });
    await expect(client2.get(request, "/api/v1/auth/me")).rejects.toThrow();
    await expect(client2.get(request, "/api/v1/auth/me")).resolves.toEqual({ role: "Admin" });
    expect(failing).toHaveBeenCalledTimes(2);
  });
});

describe("loadPageContext ordering", () => {
  it("loads permissions after the identity probe so a version change can evict first", async () => {
    const started = [];
    const backend = {
      async get(_request, path) {
        started.push(path);
        if (path === "/api/v1/auth/me") {
          return {
            role: "Department User",
            full_name: "QA User",
            user_id: "u1",
            kpi_level: "L2",
            department_id: "d1",
            position_name: "Officer",
            department_name: "Ops",
            display_name: "QA User",
            avatar_url: null,
            initials: "QU",
            avatar_color_index: 0,
          };
        }
        if (path === "/api/v1/auth/me/accounts") {
          return { user_email: "qa@example.com" };
        }
        if (path === "/api/v1/permissions/me") {
          return { permissions: { "kpi.view": true } };
        }
        throw new Error(`unexpected path ${path}`);
      },
    };

    const { base, context } = await loadPageContext({}, backend, { employee: true });

    // The identity calls run first (loadBaseContext probes /auth/me
    // fresh); permissions follow, so a changed permissions_version has
    // already evicted any stale cached permissions entry by then.
    expect(started[0]).toBe("/api/v1/auth/me");
    expect(started[started.length - 1]).toBe("/api/v1/permissions/me");
    expect(base.context.user_role).toBe("Department User");
    expect(context.permission_map).toEqual({ "kpi.view": true });
    expect(context.user_display_identity).toBe("Officer");
  });
});

describe("permissions_version companion (freshness)", () => {
  const USER = {
    role: "Department User",
    full_name: "QA User",
    user_id: "u1",
    kpi_level: "L2",
    department_id: "d1",
    position_name: "Officer",
    department_name: "Ops",
    display_name: "QA User",
    avatar_url: null,
    initials: "QU",
    avatar_color_index: 0,
  };

  function scriptedBackend(state) {
    const counts = new Map();
    const fetchImpl = async (url) => {
      const path = url.pathname;
      counts.set(path, (counts.get(path) || 0) + 1);
      if (path === "/api/v1/auth/me") {
        return jsonResponse(state.version ? { ...USER, permissions_version: state.version } : USER);
      }
      if (path === "/api/v1/auth/me/accounts") return jsonResponse({ user_email: "qa@example.com" });
      if (path === "/api/v1/permissions/me") {
        const body = { permissions: state.permissions };
        if (state.version) body.permissions_version = state.version;
        return jsonResponse(body);
      }
      if (path === "/api/v1/system/branding/public") return jsonResponse({});
      throw new Error(`unexpected path ${path}`);
    };
    return { client: createBackendClient({ fetchImpl }), counts };
  }

  it("reuses accounts and permissions while the version is unchanged", async () => {
    const state = { version: "u1-r1", permissions: { "kpi.view": true } };
    const { client, counts } = scriptedBackend(state);
    const request = fakeRequest();

    await loadPageContext(request, client, { employee: true });
    const again = await loadPageContext(request, client, { employee: true });

    // /auth/me is the freshness probe: it is re-read on every load...
    expect(counts.get("/api/v1/auth/me")).toBe(2);
    // ...but with the same version nothing else is re-fetched.
    expect(counts.get("/api/v1/auth/me/accounts")).toBe(1);
    expect(counts.get("/api/v1/permissions/me")).toBe(1);
    expect(again.context.permission_map).toEqual({ "kpi.view": true });
  });

  it("refetches the whole session context as soon as the version changes", async () => {
    const state = { version: "u1-r1", permissions: { "kpi.view": true } };
    const { client, counts } = scriptedBackend(state);
    const request = fakeRequest();

    const before = await loadPageContext(request, client, { employee: true });
    expect(before.context.permission_map).toEqual({ "kpi.view": true });

    // An admin revokes a permission: the backend bumps the version and
    // /permissions/me now answers with the smaller set.
    state.version = "u2-r1";
    state.permissions = { "kpi.view": false };

    const after = await loadPageContext(request, client, { employee: true });
    expect(after.context.permission_map).toEqual({ "kpi.view": false });
    expect(counts.get("/api/v1/permissions/me")).toBe(2);
    // The sweep also drops the session's other cached entries: accounts
    // was read in parallel with the probe on that load, so its re-fetch
    // lands on the following load.
    await loadPageContext(request, client, { employee: true });
    expect(counts.get("/api/v1/auth/me/accounts")).toBe(2);
  });

  it("keeps the plain 30s behaviour when answers carry no version", async () => {
    const state = { version: null, permissions: { "kpi.view": true } };
    const { client, counts } = scriptedBackend(state);
    const request = fakeRequest();

    await loadPageContext(request, client, { employee: true });
    await loadPageContext(request, client, { employee: true });

    expect(counts.get("/api/v1/auth/me")).toBe(2); // probe still runs
    expect(counts.get("/api/v1/permissions/me")).toBe(1); // cache hit, no eviction
  });

  it("a fresh /permissions/me answer with a new version also evicts the cached identity", async () => {
    const state = { version: "u1-r1", permissions: { "kpi.view": true } };
    const { client, counts } = scriptedBackend(state);
    const request = fakeRequest();

    await client.get(request, "/api/v1/auth/me");
    expect(counts.get("/api/v1/auth/me")).toBe(1);

    state.version = "u2-r1";
    await client.get(request, "/api/v1/permissions/me", { fresh: true });

    // The identity entry was dropped with the rest of the session context.
    await client.get(request, "/api/v1/auth/me");
    expect(counts.get("/api/v1/auth/me")).toBe(2);
  });
});
