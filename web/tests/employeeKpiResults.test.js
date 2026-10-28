import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const page = read('templates/dashboard/employee_kpi_results.njk');
const js = read('static/js/employee-kpi-results.js');
const saPage = read('templates/dashboard/superadmin_executive_dashboard.njk');
const saJs = read('static/js/superadmin-executive-dashboard.js');
const shared = read('templates/dashboard/executive_dashboard_content.njk');
const sharedJs = read('static/js/executive-dashboard.js');
const base = read('templates/layouts/base.njk');
const routes = read('web/src/routes/dashboard.js');
const fastapi = read('app/web/dashboard_web.py');

describe('Employee KPI results standalone page', () => {
  it('renders the moved section in full: heading, pagination, table, detail', () => {
    expect(page).toContain('id="employeeResultsSection"');
    expect(page).toContain('id="employeeResultsPanel"');
    expect(page).toContain('id="employeeResultsHeading"');
    expect(page).toContain('>Employee KPI results · Overview</h2>');
    expect(page).toContain('Overview — Scores are averaged across the selected range');
    expect(page).toContain('id="employeePageMeta"');
    expect(page).toContain('id="employeePrevious"');
    expect(page).toContain('id="employeeNext"');
    expect(page).toContain('id="employeeResults"');
    expect(page).toContain('id="employeeDetail"');
    expect(page).toContain('id="employeeRangeLabel"');
    expect(page).toContain('src="/static/js/employee-kpi-results.js"');
    expect(page).toContain('KPI_EXECUTIVE_CONTEXT');
    expect(page).not.toContain('onclick=');
    expect(page).not.toContain('style="');
  });

  it('loads results + detail + badges from the same APIs, paginated', () => {
    expect(js).toContain('/api/v1/dashboard/executive/employees?');
    expect(js).toContain('period_start: dateRange.start');
    expect(js).toContain('period_end: dateRange.end');
    expect(js).toContain('offset: String(employeeOffset)');
    expect(js).toContain('limit: String(employeePageSize)');
    expect(js).toContain('/api/v1/dashboard/executive/employees/${encodeURIComponent(userId)}');
    expect(js).toContain('/api/v1/kpi/records/monthly/${encodeURIComponent(userId)}');
    expect(js).toContain('/api/v1/dashboard/executive/overview');
    expect(js).toContain('selected_period');
    expect(js).toContain('function renderExceededBadges(');
    expect(js).toContain('function renderEmployeeResultRow(');
    expect(js).toContain('function renderEmployeeHistory(');
    expect(js).toContain('No KPI results are available for the selected date range.');
    expect(js).toContain('No finalized employee results in the selected range');
    // Standalone default range = the dashboard's own default (current month).
    expect(js).toContain('function getDefaultDateRange()');
    expect(js).toContain('monthNames[month - 1]');
  });

  it('removes the section and its wiring from the SA dashboard', () => {
    expect(saPage).not.toContain('id="employeeResultsSection"');
    expect(saPage).not.toContain('id="employeeResultsPanel"');
    expect(saPage).not.toContain('id="employeeResults"');
    expect(saPage).not.toContain('id="employeeDetail"');
    expect(saJs).not.toContain('async function loadEmployeeResults(');
    expect(saJs).not.toContain('async function loadEmployeeDetail(');
    expect(saJs).not.toContain('function renderExceededBadges(');
    expect(saJs).not.toContain('function runEmployeeResultsLoad(');
    expect(saJs).not.toContain('function runEmployeeDetailLoad(');
    expect(saJs).not.toContain('employeeResultsPromise');
  });

  it('leaves the Admin panel and its shared wiring untouched', () => {
    expect(shared).toContain('id="employeeResultsPanel"');
    expect(shared).toContain('>Employee KPI results · Overview</h2>');
    expect(sharedJs).toContain('async function loadEmployeeResults(');
    expect(sharedJs).toContain('function runEmployeeResultsLoad(');
  });

  it('points the SA sidebar child at the new page, Admin keeps the anchor', () => {
    expect(base).toContain("/superadmin/employee-kpi-results");
    expect(base).toContain("/admin/dashboard#employeeResultsPanel");
    expect(base).not.toContain('/superadmin/dashboard#employeeResultsSection');
    expect(base).toContain("active_page == 'employee_kpi_results'");
    const overviewAt = base.indexOf('Employee KPI Results · Overview');
    const deptAt = base.indexOf('/superadmin/department-kpi');
    expect(overviewAt).toBeGreaterThan(-1);
    expect(overviewAt).toBeLessThan(deptAt);
  });

  it('registers the page in both tiers, Super Admin only', () => {
    expect(routes).toContain('"/superadmin/employee-kpi-results"');
    expect(routes).toContain('dashboard/employee_kpi_results.njk');
    const routeAt = routes.indexOf('"/superadmin/employee-kpi-results"');
    expect(routes.slice(routeAt, routeAt + 220)).toContain('roles.superAdmin');
    expect(fastapi).toContain('"/superadmin/employee-kpi-results"');
    expect(fastapi).toContain('dashboard/employee_kpi_results.njk');
    expect(fastapi).toContain('render_employee_kpi_results_view');
    const viewAt = fastapi.indexOf('render_employee_kpi_results_view');
    const viewBody = fastapi.slice(viewAt, viewAt + 900);
    expect(viewBody).toContain('[ROLE_SUPER_ADMIN]');
    expect(viewBody).not.toContain("'Admin'");
  });
});
