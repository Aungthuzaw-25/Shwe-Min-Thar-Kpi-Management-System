import { describe, expect, it, vi } from "vitest";
import request from "supertest";
import { BackendResponseError } from "../src/lib/backendClient.js";
import { createApp } from "../src/server.js";

const profile = {
  display_name: "QA User",
  email: "qa.user@example.com",
  phone: null,
  role: "Super Admin",
  department: "QA Department",
  position: "Staff",
  avatar_url: null,
  initials: "QU",
  avatar_color_index: 2,
  password_changed_at: null,
  local_password_enabled: true,
  show_connected_accounts: true,
  linked_providers: {
    google: { email: "qa.user@gmail.com", linked_at: "2024-05-06T00:00:00" },
    microsoft: null,
  },
  configured_providers: { google: true, microsoft: false },
};

function accountBackend(role = "Super Admin") {
  return {
    get: vi.fn(async (_request, path) => {
      if (path === "/api/v1/auth/me") {
        return {
          user_id: "user-1",
          full_name: "QA User",
          role,
          kpi_level: "L2",
          department_name: "QA Department",
          position_name: "Staff",
        };
      }
      if (path === "/api/v1/auth/me/accounts") {
        return { user_email: "qa.user@example.com", local_password_enabled: true, connected_accounts: {} };
      }
      if (path === "/api/v1/account/me") return { ...profile, role, show_connected_accounts: role === "Super Admin" };
      throw new Error(`Unexpected backend route: ${path}`);
    }),
  };
}

describe("account profile page", () => {
  it("renders the account forms and connected providers only for Super Admin", async () => {
    const response = await request(createApp({ backend: accountBackend() }))
      .get("/account/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(response.text).toContain("Profile photo and details");
    expect(response.text).toContain("Change password");
    expect(response.text).toContain("Connected accounts");
    expect(response.text).toContain("Not configured by the administrator");
    expect(response.text).toContain("kpi-avatar--128");
  });

  it("renders the Active sessions card skeleton in the Security section", async () => {
    const response = await request(createApp({ backend: accountBackend() }))
      .get("/account/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(response.text).toContain("Active sessions");
    expect(response.text).toContain("sign-in sessions, not devices");
    expect(response.text).toContain("account-sessions.js");
    expect(response.text).toContain("data-sessions-list");
  });

  it("loads profile context and profile data concurrently", async () => {
    const backend = accountBackend();
    const get = backend.get;
    let activeRequests = 0;
    let maximumConcurrentRequests = 0;
    backend.get = vi.fn(async (...args) => {
      activeRequests += 1;
      maximumConcurrentRequests = Math.max(maximumConcurrentRequests, activeRequests);
      try {
        await new Promise((resolve) => setTimeout(resolve, 5));
        return await get(...args);
      } finally {
        activeRequests -= 1;
      }
    });

    const response = await request(createApp({ backend }))
      .get("/account/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(maximumConcurrentRequests).toBe(3);
  });

  it("omits the Connected accounts card for other roles", async () => {
    const response = await request(createApp({ backend: accountBackend("Department User") }))
      .get("/account/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(response.text).not.toContain("Connected accounts");
    expect(response.text).toContain("Profile photo and details");
  });

  it("redirects unauthenticated profile requests to login", async () => {
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(401, "application/json", { detail: "Not authenticated." });
      }),
    };
    const response = await request(createApp({ backend })).get("/account/profile");
    expect(response.status).toBe(307);
    expect(response.headers.location).toBe("/auth/login");
  });
});
