import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const js = readFileSync(path.join(root, 'static/js/superadmin-kpi.js'), 'utf8');
const page = readFileSync(path.join(root, 'templates/dashboard/superadmin_kpi.njk'), 'utf8');

describe('KPIs page views (subpages, not tabs)', () => {
  it('applyKpiView keeps the hidden attribute in sync with the hidden class', () => {
    const start = js.indexOf('function applyKpiView(view)');
    const end = js.indexOf('async function loadAssignmentOptions');
    const fn = js.slice(start, end);
    expect(fn).toContain('el.hidden = true');
    expect(fn).toContain('targetPanel.hidden = false');
    // The class toggles stay exactly as before.
    expect(fn).toContain("el.classList.add('hidden')");
    expect(fn).toContain("targetPanel.classList.remove('hidden')");
  });

  it('still has all four panels, wired by id, with no tab bar', () => {
    for (const panel of ['formulas', 'records', 'settings', 'assignments']) {
      expect(page).toContain(`id="${panel}-tab"`);
      expect(js).toContain(`'${panel}'`);
    }
    expect(page).not.toContain('kg-tabbar');
    expect(page).not.toContain('btn-formulas-tab');
    expect(page).not.toContain('btn-records-tab');
    expect(page).not.toContain('btn-settings-tab');
    expect(page).not.toContain('btn-assignments-tab');
    expect(page).toContain('id="kpiViewTitle"');
    expect(page).toContain('id="kpiViewSubtitle"');
  });

  it('resolves ?view=, maps legacy ?tab=, and defaults to Formula Workflow', () => {
    expect(js).toContain("function resolveKpiView()");
    expect(js).toContain("const KPI_DEFAULT_VIEW = 'formulas';");
    expect(js).toContain("return KPI_DEFAULT_VIEW;");
    expect(js).toContain("function canonicalizeKpiViewUrl(view)");
    // Canonicalizer preserves unrelated params (sets view, drops tab).
    expect(js).toContain("params.set('view', view);");
    expect(js).toContain("params.delete('tab');");
    expect(js).toContain("history.replaceState(null, '', '/superadmin/kpi?' + params.toString());");
  });

  it("loads only the current view's data (records/settings on their view)", () => {
    const start = js.indexOf('function applyKpiView(view)');
    const end = js.indexOf('async function loadAssignmentOptions');
    const fn = js.slice(start, end);
    expect(fn).toContain("if (view === 'records') loadEmployeeRecords();");
    expect(fn).toContain("if (view === 'settings') loadDepartmentSettings();");
  });
});
