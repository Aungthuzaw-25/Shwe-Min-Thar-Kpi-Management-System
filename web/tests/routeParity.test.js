import { describe, expect, it, vi } from "vitest";
import request from "supertest";
import { BackendResponseError } from "../src/lib/backendClient.js";
import { createApp } from "../src/server.js";

const accountData = {
  user_email: "qa.user@example.com",
  local_password_enabled: true,
  created_at: "2024-04-03T00:00:00",
  connected_accounts: {},
};

function backendFor(role, level = "L2") {
  return {
    get: vi.fn(async (_request, path) => {
      if (path === "/api/v1/auth/me") {
        return {
          user_id: "user-1",
          full_name: "QA User",
          role,
          kpi_level: level,
          department_id: "department-1",
          department_name: "QA Department",
          position_name: "Staff",
        };
      }
      if (path === "/api/v1/auth/me/accounts") return accountData;
      if (path === "/api/v1/permissions/me") {
        return { permissions: { "tasks.view": true, "tasks.create": true } };
      }
      throw new Error(`Unexpected backend route: ${path}`);
    }),
  };
}

const pages = [
  ["/superadmin/dashboard", ["Super Admin"]],
  ["/admin/dashboard", ["Admin", "System Admin"]],
  ["/profile", null],
  ["/superadmin/users", ["Super Admin", "Admin"]],
  ["/superadmin/super-admins", ["Super Admin"]],
  ["/superadmin/departments", ["Super Admin", "Admin"]],
  ["/superadmin/permissions", ["Super Admin", "Admin"]],
  ["/superadmin/users/user-2/profile", ["Super Admin", "Admin"]],
  ["/superadmin/kpi", ["Super Admin", "Admin"]],
  ["/superadmin/kpi-upload", ["Super Admin", "Admin", "HR"]],
  ["/superadmin/targets", ["Super Admin", "Admin"]],
  ["/superadmin/reports", ["Super Admin", "Admin"]],
  ["/positions", ["Super Admin", "Admin"]],
  ["/kpi-periods", ["Super Admin", "Admin"]],
  ["/action-plans", null],
  ["/system-settings", ["Super Admin"]],
  ["/superadmin/audit-logs", ["Super Admin"]],
  ["/superadmin/system-logs", ["Super Admin"]],
  ["/superadmin/process-tracking", ["Super Admin"]],
  ["/tasks", null],
  ["/notifications", null],
];
const roleNames = ["Super Admin", "Admin", "System Admin", "HR", "Department User"];
const pageRoleCases = pages.flatMap(([path, allowedRoles]) => (
  roleNames.map((role) => [path, role, !allowedRoles || allowedRoles.includes(role)])
));

describe("page route parity", () => {
  it("scopes the liquid-glass stylesheet to authenticated layouts", async () => {
    const adminResponse = await request(createApp({ backend: backendFor("Super Admin") }))
      .get("/superadmin/dashboard")
      .set("Cookie", "access_token=qa-token");
    const employeeResponse = await request(createApp({
      backend: backendFor("Department User", "L2"),
    }))
      .get("/dashboard")
      .set("Cookie", "access_token=qa-token");
    const kpiResponse = await request(createApp({ backend: backendFor("Super Admin") }))
      .get("/superadmin/kpi")
      .set("Cookie", "access_token=qa-token");
    const profileResponse = await request(createApp({ backend: backendFor("Department User", "L2") }))
      .get("/profile")
      .set("Cookie", "access_token=qa-token");
    const tasksResponse = await request(createApp({ backend: backendFor("Department User", "L2") }))
      .get("/tasks")
      .set("Cookie", "access_token=qa-token");
    const settingsResponse = await request(createApp({ backend: backendFor("Super Admin") }))
      .get("/system-settings")
      .set("Cookie", "access_token=qa-token");
    const loginResponse = await request(createApp({ backend: backendFor("Super Admin") }))
      .get("/auth/login");

    expect(adminResponse.text).toContain('class="app-shell app-shell--admin');
    expect(employeeResponse.text).toContain('class="app-shell app-shell--employee');
    expect(adminResponse.text).toContain("liquid-glass-theme.css?v=20261021-4");
    expect(employeeResponse.text).toContain("liquid-glass-theme.css?v=20261021-4");
    expect(kpiResponse.text).toContain("superadmin-kpi-glass.css?v=20261021-2");
    expect(profileResponse.text).toContain("ep-profile.css");
    expect(tasksResponse.text).toContain("et-tasks.css");
    expect(settingsResponse.text).toContain("system-settings-page");
    expect(adminResponse.text).not.toContain("login-form-glass.css");
    expect(employeeResponse.text).not.toContain("login-form-glass.css");
    expect(loginResponse.text).toContain("login-form-glass.css?v=20261021-2");
    expect(loginResponse.text).toContain("sessionExpiredNotice");
    expect(loginResponse.text).toContain("normalizePostLoginPath");
    expect(loginResponse.text).not.toContain("liquid-glass-theme.css");
    expect(loginResponse.text).not.toContain("app-shell");
    expect(adminResponse.text).toContain("safeLogoutFromExpiredSession");
    expect(employeeResponse.text).toContain("safeLogoutFromExpiredSession");
    expect(adminResponse.text).toContain("AUTH_REFRESH_URL");
    expect(employeeResponse.text).toContain("AUTH_REFRESH_URL");
  });

  it.each(pageRoleCases)(
    "%s returns the correct access result for %s",
    async (path, role, isAllowed) => {
      const response = await request(createApp({ backend: backendFor(role) }))
        .get(path)
        .set("Cookie", "access_token=qa-token");

      if (isAllowed) {
        expect(response.status).toBe(200);
        expect(response.headers["content-type"]).toContain("text/html");
      } else {
        expect(response.status).toBe(307);
        expect(response.headers.location).toBe("/dashboard");
      }
    },
  );

  it("routes dashboard users according to their role and normalized level", async () => {
    const redirects = [
      ["Super Admin", "L2", "/superadmin/dashboard"],
      ["Admin", "L2", "/admin/dashboard"],
    ];
    for (const [role, level, expectedLocation] of redirects) {
      const response = await request(createApp({ backend: backendFor(role, level) }))
        .get("/dashboard")
        .set("Cookie", "access_token=qa-token");
      expect(response.status).toBe(307);
      expect(response.headers.location).toBe(expectedLocation);
    }
    for (const level of ["L0", "L1", "L2", "Level 0", "Level 1", "Level 2"]) {
      const response = await request(createApp({
        backend: backendFor("Department User", level),
      }))
        .get("/dashboard")
        .set("Cookie", "access_token=qa-token");
      expect(response.status).toBe(200);
      expect(response.text).toContain("QA User");
    }

    const invalidLevel = await request(createApp({
      backend: backendFor("Department User", null),
    }))
      .get("/dashboard")
      .set("Cookie", "access_token=qa-token");
    expect(invalidLevel.status).toBe(307);
    expect(invalidLevel.headers.location).toBe("/profile");
  });

  it("redirects unauthenticated page requests to login without forwarding queries", async () => {
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(401, "application/json", {
          detail: "Not authenticated. Token missing.",
        });
      }),
    };

    const response = await request(createApp({ backend }))
      .get("/profile?token=must-not-be-forwarded");
    expect(response.status).toBe(303);
    expect(response.headers.location).toBe(
      "/auth/login?session_expired=true&next=%2Fprofile",
    );
  });
});
