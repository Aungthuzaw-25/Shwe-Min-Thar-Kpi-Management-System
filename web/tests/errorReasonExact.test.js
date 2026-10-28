import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");

function readRepo(relativePath) {
  return fs.readFileSync(path.join(repoRoot, relativePath), "utf8");
}

// Load notifications.js the way a page does at event time: the IIFE only
// defines functions and assigns window.* at the end, so a bare fake
// window/document pair is enough to exercise the helpers.
function loadNotificationsHelpers() {
  const code = readRepo("static/js/notifications.js");
  const fakeWindow = {};
  const fakeDocument = { getElementById: () => null };
  const factory = new Function("window", "document", code);
  factory(fakeWindow, fakeDocument);
  return fakeWindow;
}

function fakeResponse(status, body) {
  return {
    status,
    text: async () => body,
  };
}

describe("readServerError (shared exact-reason helper)", () => {
  const helpers = loadNotificationsHelpers();

  it("exposes the helpers on window", () => {
    expect(typeof helpers.readServerError).toBe("function");
    expect(typeof helpers.displayErrorText).toBe("function");
    expect(typeof helpers.isUnsafeErrorText).toBe("function");
  });

  it("shows the server JSON detail when it is a plain string", async () => {
    await expect(
      helpers.readServerError(fakeResponse(400, JSON.stringify({ detail: "Department name already exists." }))),
    ).resolves.toBe("Error: Department name already exists.");
  });

  it("shows the status for a non-JSON body (e.g. a 502 HTML page)", async () => {
    await expect(
      helpers.readServerError(fakeResponse(502, "<html><body>Bad Gateway</body></html>")),
    ).resolves.toBe("Server error (502)");
  });

  it("shows the status for an empty body", async () => {
    await expect(helpers.readServerError(fakeResponse(500, ""))).resolves.toBe("Server error (500)");
  });

  it("shows the status when the detail is a list (never '[object Object]')", async () => {
    await expect(
      helpers.readServerError(fakeResponse(422, JSON.stringify({ detail: [{ loc: ["name"], msg: "required" }] }))),
    ).resolves.toBe("Server error (422)");
  });

  it("shows the status when the detail looks technical (stack/SQL/internals guardrail)", async () => {
    await expect(
      helpers.readServerError(fakeResponse(500, JSON.stringify({ detail: "select * from users failed: syntax error" }))),
    ).resolves.toBe("Server error (500)");
    expect(helpers.isUnsafeErrorText("traceback (most recent call last)")).toBe(true);
    expect(helpers.isUnsafeErrorText("Department name already exists.")).toBe(false);
  });

  it("unwraps detail objects with a message or an errors list", async () => {
    await expect(
      helpers.readServerError(fakeResponse(400, JSON.stringify({ detail: { message: "Quota exceeded" } }))),
    ).resolves.toBe("Error: Quota exceeded");
    await expect(
      helpers.readServerError(fakeResponse(400, JSON.stringify({ detail: { errors: ["Name is required", "Priority is invalid"] } }))),
    ).resolves.toBe("Error: Name is required; Priority is invalid");
  });
});

describe("displayErrorText (catch-side wording)", () => {
  const helpers = loadNotificationsHelpers();

  it("maps fetch failures to the network wording only", () => {
    expect(helpers.displayErrorText(new Error("Failed to fetch"))).toBe("Network error. Please try again.");
    expect(helpers.displayErrorText(new Error("Load failed"))).toBe("Network error. Please try again.");
    expect(helpers.displayErrorText(new Error("The operation was aborted"))).toBe("Network error. Please try again.");
  });

  it("passes server/status reasons through untouched", () => {
    expect(helpers.displayErrorText(new Error("Error: Department name already exists."))).toBe(
      "Error: Department name already exists.",
    );
    expect(helpers.displayErrorText(new Error("Server error (502)"))).toBe("Server error (502)");
    expect(helpers.displayErrorText(new Error("Failed to load categories (500)"))).toBe(
      "Failed to load categories (500)",
    );
  });
});

describe("exact-reason wiring (static pins)", () => {
  it("the technical filter no longer hides reasons behind 'Something went wrong'", () => {
    const code = readRepo("static/js/notifications.js");
    expect(code).toContain('message = "Server error. Please try again.";');
    expect(code).not.toContain("Something went wrong");
  });

  it("the error page names the status instead of a generic heading", () => {
    const code = readRepo("web/src/lib/errorPage.js");
    expect(code).toContain('Server error (" + status + ")');
    expect(code).not.toContain("Something went wrong on our side");
  });

  it("fixed branches read the body through the helper, never unguarded res.json()", () => {
    const users = readRepo("templates/dashboard/users.njk");
    expect(users).not.toContain("err.detail || 'Validation failed'");
    expect(users).toContain("await window.readServerError(res)");
    const departments = readRepo("templates/dashboard/departments.njk");
    expect(departments).toContain("await window.readServerError(response)");
    const tasks = readRepo("templates/dashboard/tasks.njk");
    expect(tasks).toContain("await window.readServerError(res)");
    const permissions = readRepo("templates/dashboard/permissions.njk");
    expect(permissions).toContain("await window.readServerError(response)");
    const login = readRepo("templates/auth/login.njk");
    expect(login).toContain("Server error (${response.status})");
    const upload = readRepo("templates/dashboard/kpi_upload.njk");
    expect(upload).toContain("Server error (${response.status})");
  });

  it("the users delete/restore/permanent lines use the toast branch's local helper (toast-fix branch owns them)", () => {
    const users = readRepo("templates/dashboard/users.njk");
    // The toast branch has landed: the three delete lines no longer use
    // the old res.json() pattern — they read through the local helper.
    const owned = users.match(/else \{ showToast\(await readServerError\(res\), "error"\); \}/g) || [];
    expect(owned.length).toBe(3);
  });
});
