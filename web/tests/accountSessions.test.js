import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");

class StubEl {
  constructor(tag) {
    this.tagName = tag;
    this.className = "";
    this.textContent = "";
    this.hidden = false;
    this.disabled = false;
    this.children = [];
    this.listeners = {};
    this.dataset = {};
  }
  appendChild(child) {
    this.children.push(child);
    return child;
  }
  replaceChildren(...children) {
    this.children = children;
  }
  addEventListener(type, fn) {
    (this.listeners[type] ||= []).push(fn);
  }
  walk(fn) {
    fn(this);
    this.children.forEach((child) => child.walk(fn));
  }
  text() {
    let out = this.textContent || "";
    this.children.forEach((child) => { out += " " + child.text(); });
    return out;
  }
}

// Fixture rows in Backend order: current first, rest last_used_at desc.
// Only the seven allowed fields — never token/hash/UA/device_id.
const CURRENT = {
  id: "00000000-0000-4000-8000-000000000001",
  device_label: "Chrome on Windows",
  last_used_at: "2026-10-28T09:15:00Z",
  expires_at: "2027-01-01T00:00:00Z",
  remember_me: true,
  is_current: true,
  ip_address: "203.0.113.7",
};
const OTHER = {
  id: "00000000-0000-4000-8000-000000000002",
  device_label: "Safari on iPhone",
  last_used_at: "2026-10-27T18:40:00Z",
  expires_at: "2026-11-27T18:40:00Z",
  remember_me: false,
  is_current: false,
  ip_address: "198.51.100.23",
};

function loadModule({ rows, onDelete } = {}) {
  const statusEl = new StubEl("p");
  const listEl = new StubEl("ul");
  const emptyEl = new StubEl("p");
  const root = new StubEl("div");
  root.querySelector = (sel) => {
    if (sel === "[data-sessions-status]") return statusEl;
    if (sel === "[data-sessions-list]") return listEl;
    if (sel === "[data-sessions-empty]") return emptyEl;
    return null;
  };
  const page = new StubEl("div");
  page.dataset.appTimezone = "Asia/Yangon";
  const calls = { deletes: [], toasts: [], confirms: [] };
  let currentRows = rows === undefined ? [CURRENT, OTHER] : rows;
  const sandbox = {
    document: {
      getElementById: (id) => (id === "acctSessions" ? root : id === "accountProfilePage" ? page : null),
      createElement: (tag) => new StubEl(tag),
    },
    fetch: async (url, options = {}) => {
      if (options.method === "DELETE") {
        calls.deletes.push(url);
        if (onDelete) return onDelete(url);
        currentRows = currentRows.filter((row) => !url.endsWith(row.id));
        return { status: 200, ok: true, json: async () => ({ message: "Session revoked." }) };
      }
      return { status: 200, ok: true, json: async () => ({ sessions: currentRows }) };
    },
    location: { href: "" },
  };
  sandbox.window = {
    appConfirm: async (message, opts) => {
      calls.confirms.push({ message, opts });
      return true;
    },
    showToast: (message, kind) => { calls.toasts.push({ message, kind }); },
    readServerError: async () => "Error: Session not found.",
    location: sandbox.location,
  };
  sandbox.window.window = sandbox.window;
  const code = fs.readFileSync(path.join(repoRoot, "static/js/account-sessions.js"), "utf8");
  vm.createContext(sandbox);
  vm.runInContext(code, sandbox);
  return { sandbox, root, statusEl, listEl, emptyEl, calls };
}

const flush = () => new Promise((resolve) => setTimeout(resolve, 10));

function rowsOf(listEl) {
  return listEl.children.filter((child) => child.tagName === "li");
}

describe("account sessions card", () => {
  it("formats last-used in the panel timezone (en-GB, day + time)", () => {
    const { sandbox } = loadModule({ rows: [] });
    expect(sandbox.window.AccountSessions.formatDateTime("2026-10-28T09:15:00Z"))
      .toBe("28 Oct 2026, 15:45");
    expect(sandbox.window.AccountSessions.formatDateTime("not-a-date")).toBe("—");
  });

  it("renders Backend order: current first with badge and no revoke button, others with one", async () => {
    const { listEl, emptyEl } = loadModule();
    await flush();
    const rows = rowsOf(listEl);
    expect(rows).toHaveLength(2);
    expect(emptyEl.hidden).toBe(true);
    expect(rows[0].text()).toContain("Chrome on Windows");
    expect(rows[0].text()).toContain("This device");
    expect(rows[0].text()).toContain("Remembered");
    expect(rows[0].text()).toContain("203.0.113.7");
    expect(rows[0].text()).toContain("28 Oct 2026, 15:45");
    expect(rows[0].children.some((c) => c.tagName === "button")).toBe(false);
    expect(rows[1].text()).toContain("Safari on iPhone");
    expect(rows[1].text()).not.toContain("This device");
    const buttons = rows[1].children.filter((c) => c.tagName === "button");
    expect(buttons).toHaveLength(1);
    expect(buttons[0].textContent).toBe("Revoke");
    // The session id is never rendered as text — only a DELETE argument.
    expect(listEl.text()).not.toContain(CURRENT.id);
    expect(listEl.text()).not.toContain(OTHER.id);
    expect(listEl.text()).not.toContain("refresh_token");
    expect(listEl.text()).not.toContain("device_id");
  });

  it("revoke confirms, deletes, toasts the Backend message, and re-GETs so the row disappears", async () => {
    const { listEl, calls } = loadModule();
    await flush();
    const otherRow = rowsOf(listEl)[1];
    const button = otherRow.children.find((c) => c.tagName === "button");
    await button.listeners.click[0]();
    await flush();
    expect(calls.confirms).toHaveLength(1);
    expect(calls.confirms[0].opts.confirmText).toBe("Revoke");
    expect(calls.deletes).toHaveLength(1);
    expect(calls.deletes[0]).toContain(OTHER.id);
    expect(calls.toasts).toContainEqual({ message: "Session revoked.", kind: "success" });
    expect(rowsOf(listEl)).toHaveLength(1);
    expect(rowsOf(listEl)[0].text()).toContain("Chrome on Windows");
  });

  it("revoke failure shows the exact server reason verbatim and keeps the row", async () => {
    const { listEl, calls } = loadModule({
      onDelete: async () => ({ status: 404, ok: false, json: async () => ({ detail: "Session not found." }) }),
    });
    await flush();
    const button = rowsOf(listEl)[1].children.find((c) => c.tagName === "button");
    await button.listeners.click[0]();
    await flush();
    expect(calls.toasts).toContainEqual({ message: "Error: Session not found.", kind: "error" });
    expect(rowsOf(listEl)).toHaveLength(2);
  });

  it("shows the empty note when only the current session exists, and when none exist", async () => {
    const onlyCurrent = loadModule({ rows: [CURRENT] });
    await flush();
    expect(rowsOf(onlyCurrent.listEl)).toHaveLength(1);
    expect(onlyCurrent.emptyEl.hidden).toBe(false);
    const none = loadModule({ rows: [] });
    await flush();
    expect(rowsOf(none.listEl)).toHaveLength(0);
    expect(none.emptyEl.hidden).toBe(false);
  });
});
