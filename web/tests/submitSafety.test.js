import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (relative) => fs.readFileSync(path.join(repoRoot, relative), "utf8");

function makeStorage() {
  const map = new Map();
  return {
    get length() {
      return map.size;
    },
    key: (index) => Array.from(map.keys())[index] ?? null,
    getItem: (key) => (map.has(key) ? map.get(key) : null),
    setItem: (key, value) => map.set(key, String(value)),
    removeItem: (key) => map.delete(key),
    _map: map,
  };
}

// Evaluate a browser helper script against a fake window so tests can
// control its storage and simulate a page reload (a fresh evaluation
// with the same storage = a reload: memory is gone, storage stays).
function loadHelper(relativePath, fakeWindow) {
  const moduleStub = { exports: {} };
  const run = new Function("module", "exports", "window", read(relativePath));
  run(moduleStub, moduleStub.exports, fakeWindow);
  return moduleStub.exports;
}

function fakeWindow() {
  return {
    localStorage: makeStorage(),
    sessionStorage: makeStorage(),
    crypto: globalThis.crypto,
  };
}

describe("SubmitKey (idempotency)", () => {
  it("mints one UUID per intent and reuses it on retry", () => {
    const SubmitKey = loadHelper("static/js/submit-key.js", fakeWindow());
    const first = SubmitKey.get("task-create-self");
    expect(first).toMatch(
      /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i,
    );
    expect(SubmitKey.get("task-create-self")).toBe(first);
    expect(SubmitKey.get("target-create")).not.toBe(first);
  });

  it("survives a page reload through sessionStorage", () => {
    const window = fakeWindow();
    const first = loadHelper("static/js/submit-key.js", window).get("user-create");
    const reloaded = loadHelper("static/js/submit-key.js", window);
    expect(reloaded.get("user-create")).toBe(first);
  });

  it("clear() ends the intent — the next submit gets a fresh key", () => {
    const SubmitKey = loadHelper("static/js/submit-key.js", fakeWindow());
    const first = SubmitKey.get("task-submit-batch");
    SubmitKey.clear("task-submit-batch");
    expect(SubmitKey.get("task-submit-batch")).not.toBe(first);
  });

  it("headers() merges the key into existing headers", () => {
    const SubmitKey = loadHelper("static/js/submit-key.js", fakeWindow());
    const headers = SubmitKey.headers("kpi-calculate-custom", {
      "Content-Type": "application/json",
    });
    expect(headers["Content-Type"]).toBe("application/json");
    expect(headers["Idempotency-Key"]).toBe(SubmitKey.get("kpi-calculate-custom"));
  });

  it("classifies responses for honest UI states", () => {
    const SubmitKey = loadHelper("static/js/submit-key.js", fakeWindow());
    const response = (status, replayed) => ({
      status,
      ok: status >= 200 && status < 300,
      headers: {
        get: (name) =>
          name === "Idempotency-Replayed" ? (replayed ? "true" : null) : null,
      },
    });
    expect(SubmitKey.outcome(response(201, true))).toBe("replayed");
    expect(SubmitKey.outcome(response(200, false))).toBe("success");
    expect(SubmitKey.outcome(response(409, false))).toBe("in-progress");
    expect(SubmitKey.outcome(response(422, false))).toBe("key-conflict");
    expect(SubmitKey.outcome(response(500, false))).toBe("error");
    expect(SubmitKey.outcome(null)).toBe("unknown");
  });
});

describe("FormDraft", () => {
  function element(props) {
    return Object.assign(
      {
        id: "",
        name: "",
        type: "text",
        value: "",
        checked: false,
        disabled: false,
        readOnly: false,
        files: null,
        hasAttribute: () => false,
        dispatchEvent: () => true,
      },
      props,
    );
  }

  function container(elements) {
    return {
      querySelectorAll: () => elements,
      querySelector: (selector) => {
        if (selector.startsWith("#")) {
          return elements.find((el) => el.id === selector.slice(1)) || null;
        }
        const nameMatch = selector.match(/\[name="(.+)"\]/);
        if (nameMatch) {
          return elements.find((el) => el.name === nameMatch[1]) || null;
        }
        return null;
      },
    };
  }

  function draftWindow() {
    const window = fakeWindow();
    window.localStorage.setItem("user", JSON.stringify({ user_id: "user-42" }));
    return window;
  }

  it("collects text/checkbox values but never passwords, hidden fields or file contents", () => {
    const FormDraft = loadHelper("static/js/form-draft.js", draftWindow());
    const form = container([
      element({ id: "title", value: "Fix the pump" }),
      element({ id: "secret", type: "password", value: "do-not-save" }),
      element({ id: "token", type: "hidden", value: "abc" }),
      element({ id: "agree", type: "checkbox", checked: true }),
      element({
        id: "evidence",
        type: "file",
        files: [{ name: "photo-evidence.jpg" }],
      }),
    ]);
    const draft = FormDraft.collect(form);
    expect(draft.fields).toEqual({ title: "Fix the pump", agree: true });
    expect(draft.files).toEqual({ evidence: "photo-evidence.jpg" });
    expect(JSON.stringify(draft)).not.toContain("do-not-save");
  });

  it("seals drafts per user and clears them all on logout", () => {
    const window = draftWindow();
    const FormDraft = loadHelper("static/js/form-draft.js", window);
    const form = container([element({ id: "title", value: "Draft text" })]);
    FormDraft.save("task-form", form);
    expect(FormDraft.load("task-form").fields.title).toBe("Draft text");
    expect(FormDraft._storageKey("task-form")).toContain("user-42");

    window.localStorage.setItem("user", JSON.stringify({ user_id: "someone-else" }));
    expect(FormDraft.load("task-form")).toBeNull();

    window.localStorage.setItem("user", JSON.stringify({ user_id: "user-42" }));
    FormDraft.clearAll();
    expect(FormDraft.load("task-form")).toBeNull();
    expect(window.localStorage.getItem("user")).toContain("user-42");
  });

  it("restores saved values into the form fields", () => {
    const FormDraft = loadHelper("static/js/form-draft.js", draftWindow());
    const title = element({ id: "title", value: "" });
    const agree = element({ id: "agree", type: "checkbox", checked: false });
    const form = container([title, agree]);
    FormDraft.apply(form, {
      fields: { title: "Restored", agree: true },
      files: {},
      savedAt: Date.now(),
    });
    expect(title.value).toBe("Restored");
    expect(agree.checked).toBe(true);
  });
});

describe("draft + submit-key wiring", () => {
  it("layouts load both helpers and clear drafts on logout", () => {
    for (const layout of [
      "templates/layouts/base.njk",
      "templates/layouts/employee_base.njk",
    ]) {
      const source = read(layout);
      expect(source).toContain("/static/js/submit-key.js");
      expect(source).toContain("/static/js/form-draft.js");
      expect(source).toContain("kpiClearFormDrafts");
    }
  });

  it("every v1 submit site sends an Idempotency-Key", () => {
    const tasks = read("templates/dashboard/tasks.njk");
    expect(tasks).toContain("SubmitKey.headers('task-create-self'");
    expect(tasks).toContain("SubmitKey.headers('task-bulk-create'");
    expect(tasks).toContain("SubmitKey.headers('task-submit-batch'");
    expect(tasks).toContain("SubmitKey.headers('task-resubmit:' + taskId");
    expect(read("templates/dashboard/users.njk")).toContain("SubmitKey.headers('user-create'");
    expect(read("templates/dashboard/target.njk")).toContain("SubmitKey.headers('target-create'");
    expect(read("templates/dashboard/kpi_periods.njk")).toContain(
      "SubmitKey.headers('kpi-calculate-custom'",
    );
  });

  it("draft attach blocks exist for the five v1 forms", () => {
    expect(read("templates/dashboard/tasks.njk")).toContain("key: 'task-form'");
    expect(read("templates/dashboard/tasks.njk")).toContain("key: 'assign-task-form'");
    expect(read("templates/dashboard/users.njk")).toContain("'user-form:'");
    expect(read("templates/dashboard/target.njk")).toContain("'target-form:'");
    expect(read("templates/dashboard/kpi_periods.njk")).toContain("key: 'period-form'");
  });
});
