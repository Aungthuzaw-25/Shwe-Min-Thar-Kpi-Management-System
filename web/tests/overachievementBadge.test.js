import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const sharedJs = readFileSync(path.join(root, 'static/js/executive-dashboard.js'), 'utf8');
const saJs = readFileSync(path.join(root, 'static/js/superadmin-executive-dashboard.js'), 'utf8');
const sharedCss = readFileSync(path.join(root, 'static/css/executive-dashboard.css'), 'utf8');
const saCss = readFileSync(path.join(root, 'static/css/kd-superadmin-dashboard.css'), 'utf8');

describe('Over-achievement badge', () => {
  it.each([
    ['shared dashboard', sharedJs],
    ['superadmin dashboard', saJs],
  ])('%s renders exceeded badges in the employee detail', (name, js) => {
    expect(js).toContain('renderExceededBadges');
    expect(js).toContain('/api/v1/kpi/records/monthly/');
    expect(js).toContain('exceeded_categories');
    expect(js).toContain('entry.exceeded !== true');
    expect(js).toContain('exec-exceeded-badge');
    expect(js).toContain('score ထဲမှာ ပိုတာ မပါဘူး — bonus SA/Admin ဆုံးဖြတ်ချက်');
    // Direction-aware wording, never a raw direction code in the badge.
    expect(js).toContain('target ထက်');
    expect(js).toContain('target အောက်');
  });

  it.each([
    ['shared dashboard', sharedJs],
    ['superadmin dashboard', saJs],
  ])('%s shows badges to Super Admin / Admin only', (name, js) => {
    const fnAt = js.indexOf('function renderExceededBadges');
    const fn = js.slice(fnAt, js.indexOf('async function loadEmployeeDetail', fnAt));
    expect(fn).toContain("['SUPER ADMIN', 'ADMIN'].includes(role)");
  });

  it('keeps score/grade rendering untouched in both detail templates', () => {
    for (const js of [sharedJs, saJs]) {
      expect(js).toContain('<dt>Percentage</dt>');
      expect(js).toContain('<dt>Status</dt>');
      expect(js).toContain('Persisted KPI history');
      // Badges are a separate block after the grid, not inside a score cell.
      const detailAt = js.indexOf('target.innerHTML = `<h3>');
      const gridAt = js.indexOf('</dl>', detailAt);
      const badgeAt = js.indexOf('${exceededHtml}', detailAt);
      expect(gridAt).toBeGreaterThan(-1);
      expect(badgeAt).toBeGreaterThan(gridAt);
    }
  });

  it('styles badges with plain CSS in both dashboard stylesheets', () => {
    for (const css of [sharedCss, saCss]) {
      expect(css).toContain('.exec-exceeded-badge{');
      expect(css).toContain('.exec-exceeded-note{');
    }
  });
});
