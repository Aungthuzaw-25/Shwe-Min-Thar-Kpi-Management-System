import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";
import { createBackendClient } from "../src/lib/backendClient.js";
import { rewriteHtml } from "../src/middleware/csp.js";

const repositoryRoot = new URL("../../", import.meta.url);

describe("focused frontend security fixes", () => {
  it("preserves quoted event arguments when rewriting inline handlers for CSP", () => {
    expect(rewriteHtml(`<button onclick='record("csp-ok")'>Run</button>`, "nonce"))
      .toBe(`<button data-csp-onclick="record(&quot;csp-ok&quot;)">Run</button>`);
  });

  it("uses a 15-second abort signal for backend requests", async () => {
    const signal = new AbortController().signal;
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(signal);
    const fetchImpl = vi.fn(async () => new Response('{"ok":true}', {
      headers: { "content-type": "application/json" },
    }));
    const backend = createBackendClient({ baseUrl: "http://api.test", fetchImpl });

    try {
      await expect(backend.get({ get: () => null }, "/health")).resolves.toEqual({ ok: true });
      expect(timeout).toHaveBeenCalledWith(15_000);
      expect(fetchImpl.mock.calls[0][1].signal).toBe(signal);
    } finally {
      timeout.mockRestore();
    }
  });

  it("dispatches normalized CSP click handlers through safe listeners", async () => {
    const source = await readFile(new URL("static/js/csp-event-bridge.js", repositoryRoot), "utf8");
    const listeners = new Map();
    const calls = [];
    const document = {
      documentElement: {
        nodeType: 1,
        getAttribute: () => null,
        querySelectorAll: () => [],
      },
      addEventListener: (type, listener) => listeners.set(type, listener),
    };
    class FakeMutationObserver {
      observe() {}
    }
    const window = { record: value => calls.push(value) };
    runInNewContext(source, {
      document,
      window,
      Node: { ELEMENT_NODE: 1 },
      MutationObserver: FakeMutationObserver,
    });

    const element = { getAttribute: () => "record('csp-ok')" };
    const event = {
      target: { closest: () => element },
      preventDefault: vi.fn(),
    };
    listeners.get("click")(event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(calls).toEqual(["csp-ok"]);
  });

});
