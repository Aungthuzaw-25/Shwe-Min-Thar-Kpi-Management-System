import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const shared = read('templates/dashboard/executive_dashboard_content.njk');
const sa = read('templates/dashboard/superadmin_executive_dashboard.njk');
const split = read('templates/dashboard/employee_kpi_results.njk');
const kpis = read('templates/dashboard/superadmin_kpi.njk');

describe('KPI name split (Overview vs Manage)', () => {
  it('labels the dashboard employee section Overview on both dashboard variants', () => {
    expect(shared).toContain('>Employee KPI results · Overview</h2>');
    expect(shared).toContain('Authorized employee scope · Overview');
    // The SA section moved to its own page (employee-kpi-split): the
    // Overview heading/caption live there now, not on the SA dashboard.
    expect(split).toContain('>Employee KPI results · Overview</h2>');
    expect(split).toContain('aria-label="Employee KPI results · Overview"');
    expect(split).toContain('Overview — Scores are averaged across the selected range');
    expect(sa).not.toContain('>Employee KPI results · Overview</h2>');
    // Base wording kept, qualifier only added.
    expect(shared).not.toContain('>Employee KPI results</h2>');
    expect(split).not.toContain('>Employee KPI results</h2>');
  });

  it('labels the KPIs records tab and section Manage', () => {
    expect(kpis).toContain('>Employee KPI Records · Manage</button>');
    expect(kpis).toContain('>Employee KPI Records · Manage (Current Cycle)</h3>');
    expect(kpis).not.toContain('>Employee Records</button>');
    expect(kpis).not.toContain('>Employee KPI Records (Current Cycle)</h3>');
  });

  it('keeps the underlying hooks untouched (no function change)', () => {
    expect(shared).toContain('id="employeeResultsHeading"');
    expect(split).toContain('id="employeeResultsSection"');
    expect(split).toContain('id="employeeResultsHeading"');
    expect(sa).not.toContain('id="employeeResultsSection"');
    expect(kpis).toContain('id="btn-records-tab"');
    expect(kpis).toContain('id="records-tab"');
  });
});
