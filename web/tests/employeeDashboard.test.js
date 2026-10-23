import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

const webDirectory = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(webDirectory, "../..");
const read = (relative) => fs.readFileSync(path.join(repoRoot, relative), "utf8");

const template = read("templates/dashboard/executive_dashboard_content.njk");
const script = read("static/js/executive-dashboard.js");
const execCss = read("static/css/executive-dashboard.css");
const themeCss = read("static/css/liquid-glass-theme.css");

describe("employee dashboard: permission-less cards never render", () => {
  it("dashboard context carries the permission map (null when the page has none)", () => {
    expect(template).toMatch(/permissions: \{\{ permission_map \| default\(null\) \| tojson \}\}/);
  });

  it("panels that the script gates have stable ids", () => {
    for (const id of ["signalsPanel", "attentionPanel", "statusDistributionPanel",
      "unavailableAnalyticsPanel", "approvalPanel", "taskLifecyclePanel"]) {
      expect(template).toContain(`id="${id}"`);
    }
  });

  it("task lifecycle panel uses the house data-permission gate", () => {
    expect(template).toContain('id="taskLifecyclePanel" data-permission="tasks.view"');
  });

  it("canApprove derives from the permission map and defaults open without one", () => {
    expect(script).toMatch(/const canApprove = !permissions\s*\|\| Boolean\(permissions\['tasks\.approve'\] \|\| permissions\['kpi\.approve'\]\);/);
  });

  it("pending approvals scorecard is dropped without an approve permission", () => {
    expect(script).toContain("if (label === 'Pending approvals') return canApprove;");
  });

  it("unavailable metric cards are dropped except the headline Average KPI", () => {
    expect(script).toContain("if (metric?.status === 'unavailable') return label === 'Average KPI';");
  });

  it("empty insight panels hide and the section collapses when both are empty", () => {
    expect(script).toContain("signalsPanel.hidden = !signals?.length");
    expect(script).toContain("attentionPanel.hidden = !attentionAreas?.length");
    expect(script).toContain("insightSection.hidden = !signals?.length && !attentionAreas?.length");
  });

  it("empty status distribution hides its panel instead of an empty box", () => {
    expect(script).toContain("panel.hidden = true; refreshGridSpans();");
  });

  it("static gates: unavailable analytics hidden for personal scope, approvals hidden without permission", () => {
    expect(script).toMatch(/if \(isPersonal\) \{[\s\S]*?unavailableAnalyticsPanel/);
    expect(script).toMatch(/if \(!canApprove\) \{[\s\S]*?approvalPanel/);
  });

  it("grid span helper keeps a lone visible panel full-width (no empty gaps)", () => {
    expect(script).toContain("function refreshGridSpans()");
    expect(script).toContain("'executive-span-all', visible.length === 1 && visible[0] === panel");
    expect(execCss).toContain(".executive-span-all { grid-column: 1 / -1; }");
  });

  it("diagnostics rendering tolerates gate-removed elements", () => {
    expect(script).toMatch(/const lifecycleTarget = document\.getElementById\('taskLifecycle'\);\s*if \(lifecycleTarget\)/);
    expect(script).toMatch(/const approvalTarget = document\.getElementById\('approvalDiagnostics'\);\s*if \(approvalTarget\)/);
  });

  it("polish: denser glass scorecards in the theme layer", () => {
    expect(themeCss).toMatch(/\.app-shell \.executive-scorecard \{\s*min-height: 6\.25rem;\s*gap: \.5rem;\s*\}/);
  });
});
