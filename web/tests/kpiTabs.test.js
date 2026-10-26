import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const js = readFileSync(path.join(root, 'static/js/superadmin-kpi.js'), 'utf8');
const page = readFileSync(path.join(root, 'templates/dashboard/superadmin_kpi.njk'), 'utf8');

describe('KPIs page tabs', () => {
  it('switchTab keeps the hidden attribute in sync with the hidden class', () => {
    const start = js.indexOf('function switchTab(tabId)');
    const end = js.indexOf('async function loadAssignmentOptions');
    const fn = js.slice(start, end);
    expect(fn).toContain('el.hidden = true');
    expect(fn).toContain('targetTab.hidden = false');
    // The class toggles stay exactly as before.
    expect(fn).toContain("el.classList.add('hidden')");
    expect(fn).toContain("targetTab.classList.remove('hidden')");
  });

  it('still has all four tabs and panels wired by id', () => {
    for (const tab of ['formulas', 'records', 'settings', 'assignments']) {
      expect(page).toContain(`id="btn-${tab}-tab"`);
      expect(page).toContain(`id="${tab}-tab"`);
      expect(js).toContain(`'${tab}'`);
    }
  });
});
