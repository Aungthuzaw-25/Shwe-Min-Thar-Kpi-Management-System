import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const js = readFileSync(path.join(root, 'static/js/superadmin-kpi.js'), 'utf8');
const page = readFileSync(path.join(root, 'templates/dashboard/superadmin_kpi.njk'), 'utf8');
const css = readFileSync(path.join(root, 'static/css/superadmin-kpi-glass.css'), 'utf8');

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

  it('Records view spans the full page width (no capped container)', () => {
    // 2026-11-14: .kg-container was max-width:1120px + margin auto, so
    // on a wide screen the whole KPI page (header + all four views)
    // sat narrow and centred with a big empty area around it — the
    // "ugly and wrong format" Zenn reported on Records. The Users page
    // has no capped container, so neither does this one now.
    const start = css.indexOf('.kg-page .kg-container{');
    const end = css.indexOf('}', start);
    const rule = css.slice(start, end);
    expect(rule).toContain('width:100%');
    expect(rule).not.toContain('max-width:1120px');
  });

  it('Records card header shows the record count (Users format)', () => {
    expect(page).toContain('All records <span id="recordsCount">0</span>');
    // Updated from the already-fetched array — no extra request, and
    // it runs before the empty branch, so 0 records shows "All records 0".
    const start = js.indexOf('async function loadEmployeeRecords()');
    const fn = js.slice(start, js.indexOf('function openIntervention', start));
    expect(fn).toContain('setRecordsCount(records.length)');
  });

  it('the two form cards keep their own narrow width (readable forms)', () => {
    // Uncapping the page container must not widen Role Tracking /
    // Assign Tasks: they are forms, so their max-w-4xl stays.
    expect(page.match(/max-w-4xl mx-auto/g)).toHaveLength(2);
  });

  it('Formula Workflow shows the formula count (Users format)', () => {
    expect(page).toContain('All formulas <span id="formulaCount">0</span>');
    const start = js.indexOf('async function loadDepartmentKPIs()');
    const fn = js.slice(start, js.indexOf('async function workflowAction', start));
    expect(fn).toContain('setFormulaCount(formulas.length)');
  });

  it('Role Tracking shows the department count (Users format)', () => {
    expect(page).toContain('All departments <span id="deptCount">0</span>');
    const start = js.indexOf('async function loadDepartmentSettings()');
    const fn = js.slice(start, js.indexOf('async function saveDeptLevelSettings', start));
    expect(fn).toContain('setDeptCount(Array.isArray(depts) ? depts.length : 0)');
  });

  it('Assign Tasks stays a form (no count, no table — Users format has nothing to add)', () => {
    const start = page.indexOf('id="assignments-tab"');
    const panel = page.slice(start, page.indexOf('id="formulasModal"', start));
    expect(panel).toContain('id="assignmentForm"');
    expect(panel).not.toContain('Count');
  });
});
