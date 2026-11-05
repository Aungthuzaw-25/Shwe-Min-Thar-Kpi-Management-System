import fs from "node:fs";
import path from "node:path";
import vm from "node:vm";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const bridge = fs.readFileSync(path.join(repoRoot, "static/js/csp-event-bridge.js"), "utf8");

function readTree(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...readTree(full));
    else if (entry.isFile()) out.push(full);
  }
  return out;
}

// Re-derive the handler inventory from the repo the same way the allowlist
// was built: every inline on* attribute in templates + JS-built rows.
function deriveInventory() {
  const files = [
    ...readTree(path.join(repoRoot, "templates")),
    ...fs.readdirSync(path.join(repoRoot, "static/js"))
      .filter((f) => f.endsWith(".js"))
      .map((f) => path.join(repoRoot, "static/js", f)),
  ];
  const names = new Set();
  const unparsedCalls = [];
  const attrPattern = /on(click|submit|change|keydown)\s*=\s*"([^"]*)"/g;
  for (const file of files) {
    const text = fs.readFileSync(file, "utf8");
    for (const match of text.matchAll(attrPattern)) {
      for (const part of match[2].split(";")) {
        const trimmed = part.trim();
        if (!trimmed || trimmed === "this.parentElement.remove()") continue;
        const name = /^(?:if\s*\(.*?\)\s*)?([A-Za-z_$][A-Za-z0-9_$]*)\s*\(/.exec(trimmed);
        if (name) names.add(name[1]);
        else if (trimmed.includes("(")) unparsedCalls.push(`${file}: ${trimmed}`);
      }
    }
  }
  return { names, unparsedCalls };
}

function bridgeAllowlist() {
  const body = bridge.split("new Set([")[1].split("])")[0];
  const entries = [...body.matchAll(/"([A-Za-z_$][A-Za-z0-9_$]*)"/g)].map((m) => m[1]);
  return entries;
}

describe("CSP bridge handler allowlist", () => {
  it("gates dispatch on the allowlist, not on any window global", () => {
    expect(bridge).toContain("!ALLOWED_HANDLERS.has(functionName)");
    expect(bridge).toContain("ALLOWED_HANDLERS = new Set([");
  });

  it("matches the repo inventory exactly: no missing handler, no dead entry", () => {
    const { names, unparsedCalls } = deriveInventory();
    expect(unparsedCalls).toEqual([]);
    const entries = bridgeAllowlist();
    expect(entries.length).toBe(new Set(entries).size);
    expect(new Set(entries)).toEqual(names);
  });

  it("keeps the fixed DOM-remove action (not a function dispatch)", () => {
    expect(bridge).toContain('trimmed === "this.parentElement.remove()"');
  });
});

function loadBridge() {
  const listeners = {};
  const sandbox = {
    Node: { ELEMENT_NODE: 1 },
    MutationObserver: class {
      constructor(callback) { sandbox.__observerCallback = callback; }
      observe() {}
    },
    document: {
      documentElement: {
        nodeType: 1,
        querySelectorAll: () => [],
        _attrs: {},
        getAttribute: function (name) { return this._attrs[name] ?? null; },
        setAttribute: function (name, value) { this._attrs[name] = value; },
        removeAttribute: function (name) { delete this._attrs[name]; },
      },
      addEventListener: (type, fn) => { listeners[type] = fn; },
    },
  };
  sandbox.window = sandbox;
  sandbox.__listeners = listeners;
  vm.runInNewContext(bridge, sandbox);
  return sandbox;
}

function fakeElement(handler, attrs = {}) {
  const el = {
    _attrs: { ...attrs },
    getAttribute: function (name) { return this._attrs[name] ?? null; },
    setAttribute: function (name, value) { this._attrs[name] = value; },
    removeAttribute: function (name) { delete this._attrs[name]; },
    querySelectorAll: () => [],
    nodeType: 1,
    parentElement: null,
    closest: function (selector) {
      return selector === "[data-csp-onclick]" && this._attrs["data-csp-onclick"] ? this : null;
    },
  };
  if (handler !== null) el._attrs["data-csp-onclick"] = handler;
  return el;
}

describe("CSP bridge dispatch behaviour (vm harness)", () => {
  it("dispatches allowlisted names with quoted-string and this args", () => {
    const sandbox = loadBridge();
    const trace = [];
    sandbox.saveUser = (...args) => trace.push(["saveUser", ...args]);
    sandbox.closeModal = (...args) => trace.push(["closeModal", ...args]);
    const click = sandbox.__listeners.click;
    click({ target: fakeElement("saveUser('abc')"), preventDefault: () => {} });
    click({ target: fakeElement("closeModal(this)"), preventDefault: () => {} });
    expect(trace[0]).toEqual(["saveUser", "abc"]);
    expect(trace[1][0]).toBe("closeModal");
    expect(trace[1][1]).toBeTruthy();
  });

  it("refuses non-allowlisted window globals (silent no-op)", () => {
    const sandbox = loadBridge();
    const trace = [];
    sandbox.evilFn = (...args) => trace.push(["evilFn", ...args]);
    sandbox.fetch = (...args) => trace.push(["fetch", ...args]);
    const click = sandbox.__listeners.click;
    click({ target: fakeElement("evilFn('x')"), preventDefault: () => {} });
    click({ target: fakeElement("fetch('https://evil.example/')"), preventDefault: () => {} });
    expect(trace).toEqual([]);
  });

  it("still converts MutationObserver-added nodes for allowlisted names", () => {
    const sandbox = loadBridge();
    const trace = [];
    sandbox.closeModal = () => trace.push("closeModal");
    const el = fakeElement(null, { onclick: "closeModal()" });
    sandbox.__observerCallback([{ addedNodes: [el] }]);
    expect(el._attrs["data-csp-onclick"]).toBe("closeModal()");
    expect(el._attrs.onclick).toBeUndefined();
    sandbox.__listeners.click({ target: el, preventDefault: () => {} });
    expect(trace).toEqual(["closeModal"]);
  });
});
