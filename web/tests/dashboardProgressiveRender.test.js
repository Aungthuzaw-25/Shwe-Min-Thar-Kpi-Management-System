import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..", "..");
const js = readFileSync(path.join(root, "static/js/superadmin-executive-dashboard.js"), "utf8");
const loadDashboard = js.slice(
  js.indexOf("async function loadDashboard()"),
  js.indexOf("function runDashboardLoad()"),
);

describe("dashboard progressive render (perf/lcp Phase 6)", () => {
  it("renders executive sections as soon as executive resolves, before awaiting the summary", () => {
    const summaryAwait = loadDashboard.indexOf("await summaryPromise");
    expect(summaryAwait).toBeGreaterThan(-1);
    for (const call of [
      "renderKpiStatusDistribution(",
      "renderDepartments(",
      "renderExecutiveSignals(",
      "renderDataQuality(",
    ]) {
      const at = loadDashboard.indexOf(call);
      expect(at, call).toBeGreaterThan(-1);
      expect(at, `${call} must run before the summary await`).toBeLessThan(summaryAwait);
    }
  });

  it("renders scorecards twice: executive data first, full data on summary resolve", () => {
    const calls = [...loadDashboard.matchAll(/renderScorecards\(\{/g)];
    expect(calls).toHaveLength(2);
    const summaryAwait = loadDashboard.indexOf("await summaryPromise");
    expect(calls[0].index).toBeLessThan(summaryAwait);
    expect(calls[1].index).toBeGreaterThan(summaryAwait);
    // The summary-stage render still passes the full summary payload,
    // so the final UI is identical to the old render-everything-at-once.
    const second = loadDashboard.slice(calls[1].index);
    expect(second).toContain("payload.scorecards");
    expect(second).toContain("payload.activity_summary");
    expect(second).toContain("payload.task_diagnostics");
  });

  it("keeps sequencing, abort, auth-error and partial-update handling", () => {
    // A superseded load renders nothing after either await: one guard
    // between the executive await and the summary await, one after it
    // (a third, in the catch block, guards the error path).
    const guard = "if (sequence !== requestSequence) return;";
    const executiveAwait = loadDashboard.indexOf("await executivePromise");
    const summaryAwait = loadDashboard.indexOf("await summaryPromise");
    const firstGuard = loadDashboard.indexOf(guard, executiveAwait);
    expect(firstGuard).toBeGreaterThan(-1);
    expect(firstGuard).toBeLessThan(summaryAwait);
    expect(loadDashboard.indexOf(guard, summaryAwait)).toBeGreaterThan(-1);
    expect(loadDashboard).toContain("controller.abort()");
    expect(loadDashboard).toContain("summaryError?.status === 401");
    expect(loadDashboard).toContain("Partially updated");
    expect(loadDashboard).toContain("root.classList.remove('exec-is-loading')");
  });
});
