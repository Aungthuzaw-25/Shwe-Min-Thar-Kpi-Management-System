import { describe, expect, it, vi } from "vitest";
import request from "supertest";
import nunjucks from "nunjucks";
import { createBackendClient } from "../src/lib/backendClient.js";
import { createApp } from "../src/server.js";
import { registerFilters } from "../src/lib/nunjucks_filters.js";

const fakeAccountData = {
  user_email: "qa.user@example.com",
  local_password_enabled: true,
  created_at: "2024-04-03T00:00:00",
  connected_accounts: {
    google: {
      email: "qa.user@gmail.com",
      connected_at: "2024-05-06T00:00:00",
    },
  },
};

function fakeBackend(role = "Department User", userLevel = "L2") {
  return {
    get: vi.fn(async (_request, path) => {
      if (path === "/api/v1/auth/me") {
        return {
          user_id: "user-1",
          full_name: "QA User",
          role,
          kpi_level: userLevel,
          department_id: "department-1",
          department_name: "QA Department",
          position_name: "Staff",
        };
      }
      if (path === "/api/v1/auth/me/accounts") return fakeAccountData;
      if (path === "/api/v1/permissions/me") {
        return { permissions: { "tasks.view": true, "tasks.create": false } };
      }
      throw new Error(`Unexpected backend route: ${path}`);
    }),
  };
}

describe("Nunjucks filters", () => {
  it("escapes HTML-significant and JavaScript line-separator characters in JSON", () => {
    const environment = new nunjucks.Environment();
    registerFilters(environment, nunjucks);
    const result = environment.getFilter("tojson")(
      "</script>&\u2028\u2029",
    ).toString();

    expect(result).toBe('"\\u003c/script\\u003e\\u0026\\u2028\\u2029"');
  });

  it("formats ISO date strings without shifting their calendar date", () => {
    const environment = new nunjucks.Environment();
    registerFilters(environment, nunjucks);

    expect(environment.getFilter("strftime")("2024-02-03T23:30:00Z", "%Y-%m-%d"))
      .toBe("2024-02-03");
  });
});

describe("Express pages and CSP middleware", () => {
  it("renders the login page with nonce and inline-handler rewrites", async () => {
    const response = await request(createApp({
      environment: "development",
      s3Endpoint: "https://storage.example:9443",
    })).get("/auth/login");

    expect(response.status).toBe(200);
    expect(response.headers["x-content-type-options"]).toBe("nosniff");
    expect(response.headers["x-frame-options"]).toBe("DENY");
    const nonce = response.headers["content-security-policy"].match(/nonce-([^']+)/)?.[1];
    expect(nonce).toBeTruthy();
    expect(response.headers["content-security-policy"]).toBe([
      "default-src 'self';",
      `script-src 'self' 'nonce-${nonce}';`,
      `style-src 'self' 'nonce-${nonce}' https://fonts.googleapis.com;`,
      "font-src 'self' https://fonts.gstatic.com;",
      "img-src 'self' data: blob: https://storage.example:9443 http://localhost:9000 http://minio:9000;",
      "connect-src 'self' https://storage.example:9443 http://localhost:9000 http://minio:9000;",
      "frame-ancestors 'none';",
    ].join(" "));
    expect(response.text).toContain(`<script nonce="${nonce}"`);
    expect(response.text).toContain(`<style nonce="${nonce}"`);
    expect(response.text).toContain("data-csp-onclick=");
  });

  it("preserves OAuth and post-login query-string handling on public pages", async () => {
    const app = createApp();
    const login = await request(app)
      .get("/auth/login?oauth_error=provider_denied&next=%2Fdashboard");
    const changePassword = await request(app)
      .get("/auth/change-password?next=%2Fadmin%2Fdashboard");

    expect(login.status).toBe(200);
    expect(login.text).toContain("new URLSearchParams(window.location.search).get('oauth_error')");
    expect(changePassword.status).toBe(200);
    expect(changePassword.text).toContain("new URLSearchParams(window.location.search).get('next')");
  });

  it("passes cookies and authorization through to the authentication API", async () => {
    const fetchImpl = vi.fn(async (url) => new Response(JSON.stringify(
      String(url).endsWith("/accounts")
        ? fakeAccountData
        : {
            user_id: "user-1",
            full_name: "QA User",
            role: "Super Admin",
            kpi_level: null,
          },
    ), { headers: { "content-type": "application/json" } }));
    const backend = createBackendClient({
      baseUrl: "http://api.test",
      fetchImpl,
    });
    const app = createApp({ backend });
    await request(app)
      .get("/superadmin/dashboard")
      .set("Cookie", "access_token=qa-token")
      .set("Authorization", "Bearer qa-header")
      .expect(200);
    expect(fetchImpl).toHaveBeenCalledTimes(2);
    for (const [, options] of fetchImpl.mock.calls) {
      expect(options.headers.get("cookie")).toBe("access_token=qa-token");
      expect(options.headers.get("authorization")).toBe("Bearer qa-header");
    }
  });

  it("renders the profile page using the authenticated context and account data", async () => {
    const response = await request(createApp({ backend: fakeBackend("Super Admin") }))
      .get("/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(response.text).toContain("qa.user@example.com");
    expect(response.text).toContain("qa.user@gmail.com");
    expect(response.text).toContain("Connected on 06 May 2024");
  });

  it("does not render the legacy Connected Accounts card for other roles", async () => {
    const response = await request(createApp({ backend: fakeBackend("Department User") }))
      .get("/profile")
      .set("Cookie", "access_token=qa-token");

    expect(response.status).toBe(200);
    expect(response.text).not.toContain("Connected Accounts");
  });

  it("redirects expired page sessions to login with a safe return path", async () => {
    const { BackendResponseError } = await import("../src/lib/backendClient.js");
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(401, "application/json", {
          detail: "Token expired or invalid.",
        });
      }),
    };

    const response = await request(createApp({ backend }))
      .get("/dashboard?token=must-not-be-forwarded");
    expect(response.status).toBe(303);
    expect(response.headers.location).toBe(
      "/auth/login?session_expired=true&next=%2Fdashboard",
    );
  });

  it("preserves authenticated forbidden responses instead of redirecting", async () => {
    const { BackendResponseError } = await import("../src/lib/backendClient.js");
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(403, "application/json", {
          detail: "Access denied.",
        });
      }),
    };

    const response = await request(createApp({ backend })).get("/dashboard");
    expect(response.status).toBe(403);
    expect(response.headers["content-type"]).toMatch(/^text\/html/);
    expect(response.text).toContain("Access denied");
    expect(response.text).not.toContain("Access denied.");
  });

  it("does not accept an external return URL from the page query", async () => {
    const { BackendResponseError } = await import("../src/lib/backendClient.js");
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(401, "application/json", {
          detail: "Not authenticated.",
        });
      }),
    };

    const response = await request(createApp({ backend }))
      .get("/dashboard?next=https%3A%2F%2Fevil.example%2F");
    expect(response.status).toBe(303);
    expect(response.headers.location).toBe(
      "/auth/login?session_expired=true&next=%2Fdashboard",
    );
  });

  it("HTML-escapes strings in JSON error responses", async () => {
    const { BackendResponseError } = await import("../src/lib/backendClient.js");
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(400, "application/json", {
          detail: '<script>alert("xss")</script>',
          nested: ["<img src=x onerror='attack'>"],
        });
      }),
    };

    const response = await request(createApp({ backend })).get("/dashboard");

    expect(response.status).toBe(400);
    expect(response.body).toEqual({
      detail: "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;",
      nested: ["&lt;img src=x onerror=&#39;attack&#39;&gt;"],
    });
  });

  it("returns backend string errors as sanitized JSON instead of executable HTML", async () => {
    const { BackendResponseError } = await import("../src/lib/backendClient.js");
    const backend = {
      get: vi.fn(async () => {
        throw new BackendResponseError(
          400,
          "text/html",
          '<script>alert("xss")</script>',
        );
      }),
    };

    const response = await request(createApp({ backend })).get("/dashboard");

    expect(response.status).toBe(400);
    expect(response.headers["content-type"]).toMatch(/^application\/json/);
    expect(response.body).toEqual({
      detail: "&lt;script&gt;alert(&quot;xss&quot;)&lt;/script&gt;",
    });
  });

  it("writes request failures as single-line structured logs", async () => {
    const log = vi.spyOn(console, "error").mockImplementation(() => {});
    const backend = {
      get: vi.fn(async () => {
        throw new Error("failure\r\nforged log entry");
      }),
    };

    try {
      await request(createApp({ backend })).get("/dashboard");

      expect(log).toHaveBeenCalledOnce();
      expect(log.mock.calls[0][0]).toContain('"error":"failure__forged log entry"');
      expect(log.mock.calls[0][0]).not.toMatch(/[\r\n]/);
    } finally {
      log.mockRestore();
    }
  });
});
