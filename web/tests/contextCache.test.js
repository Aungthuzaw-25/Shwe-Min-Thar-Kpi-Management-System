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

describe("loadPageContext (Batch 2)", () => {
  it("starts the permissions call together with the identity calls", async () => {
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

    expect(started[0]).toBe("/api/v1/permissions/me");
    expect(base.context.user_role).toBe("Department User");
    expect(context.permission_map).toEqual({ "kpi.view": true });
    expect(context.user_display_identity).toBe("Officer");
  });
});
