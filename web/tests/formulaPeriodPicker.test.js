import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (relative) => fs.readFileSync(path.join(repoRoot, relative), "utf8");

const template = read("templates/dashboard/superadmin_kpi.njk");
const script = read("static/js/superadmin-kpi.js");
const notifications = read("static/js/notifications.js");

describe("formula period picker + lifecycle UI", () => {
  it("the builder form carries the period picker section and a labelled save button", () => {
    expect(template).toContain('id="fPeriodsList"');
    expect(template).toContain('id="fPeriodsHint"');
    expect(template).toContain("Applies to Periods");
    expect(template).toContain('id="saveFormulaBtn"');
  });

  it("the picker loads every period page and pre-checks the formula's current links", () => {
    expect(script).toContain("`${API_BASE}/periods?skip=${skip}&limit=100`");
    expect(script).toContain("selected.has(String(p.id))");
    expect(script).toContain("loadFormulaPeriods(f.period_ids || [])");
  });

  it("a picker load failure never wipes the current links on save", () => {
    expect(script).toContain("formulaPeriodsLoadFailed = true");
    expect(script).toContain("if (!formulaPeriodsLoadFailed) {");
    expect(script).toContain("payload.period_ids = selectedFormulaPeriodIds();");
  });

  it("revive always starts with an empty picker and refuses an empty save client-side", () => {
    expect(script).toContain('loadFormulaPeriods([]);');
    expect(script).toContain("Choose at least one period to revive this formula.");
    expect(script).toContain("`${API_BASE}/formulas/${fId}/revive`");
  });

  it("closed cards offer only view + reuse, open cards offer close before delete", () => {
    expect(script).toContain('data-view-formula=');
    expect(script).toContain('data-revive-formula=');
    expect(script).toContain('data-close-formula=');
    expect(script).toContain("Closed${closedDate");
    expect(script).toContain("No periods linked");
    expect(script).toContain("not used in template downloads or calculations");
  });

  it("the closing-soon / closed notifications link straight to the formula's period picker", () => {
    expect(notifications).toContain('"KPI formula closing soon"');
    expect(notifications).toContain('"KPI formula closed"');
    expect(notifications).toContain("?formula=");
    expect(script).toContain('openFormulaFromQuery()');
  });

  it("no new inline style attributes were added to the formula page", () => {
    const styled = template.match(/<[^>]+style="/g) || [];
    expect(styled).toHaveLength(0);
  });
});
