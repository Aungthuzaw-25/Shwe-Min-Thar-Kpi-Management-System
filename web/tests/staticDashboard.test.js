import { readFile } from "node:fs/promises";
import { runInNewContext } from "node:vm";
import { describe, expect, it, vi } from "vitest";

const repositoryRoot = new URL("../../", import.meta.url);

async function loadDashboardScript(fetchImpl) {
  const source = await readFile(new URL("static/js/dashboard.js", repositoryRoot), "utf8");
  const inputListeners = new Map();
  const search = {
    value: "",
    addEventListener: (type, listener) => inputListeners.set(type, listener),
  };
  const row = {
    textContent: "Alpha employee",
    classList: { toggle: vi.fn() },
  };
  const notificationList = { replaceChildren: vi.fn() };
  const badge = {
    classList: { add: vi.fn(), remove: vi.fn() },
  };
  const intervals = [];
  const timeouts = new Map();
  let nextTimeout = 0;
  const window = {
    KPI_CONTEXT: {},
    location: { pathname: "/employees" },
    setTimeout: callback => {
      const id = ++nextTimeout;
      timeouts.set(id, callback);
      return id;
    },
    clearTimeout: id => timeouts.delete(id),
  };
  const document = {
    cookie: "",
    getElementById: id => ({
      globalSearch: search,
      notificationList,
      notifBadge: badge,
    })[id] || null,
    querySelectorAll: selector => selector === "#sidebarNav .list-group-item[data-page]"
      ? []
      : selector === "table tbody tr" ? [row] : [],
    querySelector: () => null,
    createElement: () => ({
      classList: { add: vi.fn() },
      addEventListener: vi.fn(),
      append: vi.fn(),
      setAttribute: vi.fn(),
    }),
  };

  runInNewContext(source, {
    document,
    window,
    fetch: fetchImpl,
    setInterval: callback => {
      intervals.push(callback);
      return intervals.length;
    },
    console: { error: vi.fn(), log: vi.fn() },
  });

  return { window, search, inputListeners, row, intervals, timeouts };
}

describe("static dashboard request and search behavior", () => {
  it("coalesces notification polling and repeated mark-all clicks in flight", async () => {
    let resolveNotifications;
    const fetchImpl = vi.fn(url => {
      if (url === "/api/v1/notifications?limit=5") {
        return new Promise(resolve => {
          resolveNotifications = () => resolve({ ok: true, json: async () => [] });
        });
      }
      return Promise.resolve({ ok: true });
    });
    const { window, intervals } = await loadDashboardScript(fetchImpl);

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    intervals[0]();
    const firstClick = window.markAllNotifsRead();
    const secondClick = window.markAllNotifsRead();

    expect(fetchImpl.mock.calls.filter(([, options]) => options?.method === "PUT")).toHaveLength(1);
    expect(fetchImpl.mock.calls.filter(([url]) => url === "/api/v1/notifications?limit=5")).toHaveLength(1);

    resolveNotifications();
    await Promise.all([firstClick, secondClick]);
  });

  it("debounces global table filtering on input", async () => {
    const fetchImpl = vi.fn(async () => ({ ok: true, json: async () => [] }));
    const { search, inputListeners, row, timeouts } = await loadDashboardScript(fetchImpl);
    const onInput = inputListeners.get("input");

    search.value = "Al";
    onInput.call(search);
    search.value = "Beta";
    onInput.call(search);

    expect(row.classList.toggle).not.toHaveBeenCalled();
    expect(timeouts.size).toBe(1);
    [...timeouts.values()][0]();

    expect(row.classList.toggle).toHaveBeenCalledWith("is-hidden", true);
  });
});
