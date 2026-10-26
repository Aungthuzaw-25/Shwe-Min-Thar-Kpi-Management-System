import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const page = read('templates/dashboard/department_kpi.njk');
const js = read('static/js/department-kpi.js');
const base = read('templates/layouts/base.njk');
const routes = read('web/src/routes/dashboard.js');
const fastapi = read('app/web/dashboard_web.py');

describe('Department KPI page', () => {
  it('renders department + period selectors and the ranked table', () => {
    expect(page).toContain('id="deptKpiDepartment"');
    expect(page).toContain('id="deptKpiPeriod"');
    expect(page).toContain('id="deptKpiBody"');
    expect(page).toContain('id="deptKpiMore"');
    expect(page).toContain('>Rank</th>');
    expect(page).toContain('>Score</th>');
    expect(page).toContain('>Grade</th>');
    expect(page).toContain('Select a department to view its ranked KPI results.');
    expect(page).toContain('src="/static/js/department-kpi.js"');
    expect(page).not.toContain('onclick=');
    expect(page).not.toContain('style="');
  });

  it('orders highest score first and pages Top 10 by default', () => {
    expect(js).toContain('const PAGE_SIZE = 10;');
    expect(js).toContain('Number(b.percentage || 0) - Number(a.percentage || 0)');
    expect(js).toContain('limit: String(PAGE_SIZE)');
    expect(js).toContain('offset: String(offset)');
    expect(js).toContain('/api/v1/dashboard/executive/employees?');
    expect(js).toContain('department_id: departmentId');
    expect(js).toContain('period_id: periodId');
    expect(js).toContain('/api/v1/departments');
    expect(js).toContain('/api/v1/dashboard/executive/overview');
    expect(js).toContain('selected_period');
    // Out-of-scope department (404) reads as "no results", not an error wall.
    expect(js).toContain("response.status === 404");
    expect(js).toContain('No results for this department in the selected period.');
  });

  it('adds the Dashboard submenu with Overview above Department KPI', () => {
    const overviewAt = base.indexOf('Employee KPI Results · Overview');
    const deptAt = base.indexOf('/superadmin/department-kpi');
    expect(overviewAt).toBeGreaterThan(-1);
    expect(deptAt).toBeGreaterThan(-1);
    expect(overviewAt).toBeLessThan(deptAt);
    expect(base).toContain('#employeeResultsSection');
    expect(base).toContain('#employeeResultsPanel');
    expect(base).toContain("active_page == 'department_kpi'");
  });

  it('registers the page in both tiers for Super Admin + Admin', () => {
    expect(routes).toContain('"/superadmin/department-kpi"');
    expect(routes).toContain('dashboard/department_kpi.njk');
    expect(fastapi).toContain('"/superadmin/department-kpi"');
    expect(fastapi).toContain('dashboard/department_kpi.njk');
    expect(fastapi).toContain('render_department_kpi_view');
  });
});
