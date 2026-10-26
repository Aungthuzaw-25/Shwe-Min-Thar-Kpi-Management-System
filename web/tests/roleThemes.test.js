import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const themeCss = read('static/css/role-themes.css');
const base = read('templates/layouts/base.njk');
const employeeBase = read('templates/layouts/employee_base.njk');

describe('role-family themes', () => {
  it('loads role-themes.css after the glass theme in both layouts', () => {
    for (const layout of [base, employeeBase]) {
      const glass = layout.indexOf('liquid-glass-theme.css');
      const theme = layout.indexOf('role-themes.css');
      expect(glass).toBeGreaterThan(-1);
      expect(theme).toBeGreaterThan(glass);
    }
  });

  it('maps the viewer role family onto the body class in both layouts', () => {
    for (const layout of [base, employeeBase]) {
      expect(layout).toContain("user_role == 'Super Admin' %}theme-sa");
      expect(layout).toContain("user_role in ['Admin', 'HR', 'System Admin'] %}theme-admin");
      expect(layout).toContain("{% elif user_role %}theme-employee");
    }
  });

  it('carries every palette colour for all three families', () => {
    const lower = themeCss.toLowerCase();
    for (const hex of ['0a1931', '1a3d63', '4a7fa7', 'b3cfe5', 'f6fafd']) {
      expect(lower).toContain(`#${hex}`);
    }
    for (const hex of ['edc097', 'bb5c38', '5c3d2e', '2d2424']) {
      expect(lower).toContain(`#${hex}`);
    }
    for (const hex of ['ffca03', 'f39500', 'cd1818', '0f2c67']) {
      expect(lower).toContain(`#${hex}`);
    }
  });

  it('themes sidebar, topbar, primary buttons and active states per family', () => {
    for (const family of ['theme-sa', 'theme-admin', 'theme-employee']) {
      expect(themeCss).toContain(`.app-shell.${family} #mainSidebar`);
      expect(themeCss).toContain(`.app-shell.${family} .app-topbar`);
      expect(themeCss).toContain(`.app-shell.${family} main .bg-blue-950`);
      expect(themeCss).toContain(`.app-shell.${family} #mainSidebar nav a.active`);
    }
  });

  it('keeps the search pill treatment on search inputs only', () => {
    expect(themeCss).toContain('input[placeholder*="Search"]');
    expect(themeCss).toContain('border-radius: 999px');
    expect(themeCss).not.toContain('.search-page');
  });

  it('never touches login selectors, canvases or chart elements', () => {
    expect(themeCss).not.toMatch(/[.#]login/);
    expect(themeCss).not.toContain('login.njk');
    expect(themeCss).not.toContain('canvas');
    expect(themeCss).not.toContain('kd-donut');
  });

  it('puts the search icon on the right everywhere', () => {
    expect(themeCss).toContain('background-position: right 14px center');
    expect(themeCss).toContain('data:image/svg+xml');
    expect(read('templates/dashboard/permissions.njk')).not.toContain('🔍');
    for (const file of ['users', 'departments', 'audit_logs']) {
      expect(read(`templates/dashboard/${file}.njk`)).toContain('absolute right-3');
    }
    const tasks = read('templates/dashboard/tasks.njk');
    for (const chunk of tasks.split('<label class="et-search">').slice(1)) {
      const label = chunk.split('</label>')[0];
      expect(label.indexOf('<input')).toBeLessThan(label.indexOf('<svg'));
    }
    const profile = read('templates/dashboard/profile.njk');
    const search = profile.split('ep-permission-search">')[1].split('</div>')[0];
    expect(search.indexOf('<input')).toBeLessThan(search.indexOf('<svg'));
  });

  it('gives the tasks and profile search wrappers the same pill and glow', () => {
    expect(themeCss).toContain('.et-page .et-search,');
    expect(themeCss).toContain('.ep-page .ep-permission-search');
    expect(themeCss).toContain(':focus-within');
    expect(themeCss).toContain('prefers-reduced-motion');
  });

  it('uses !important only for the search pill and the tasks primary button', () => {
    const withoutComments = themeCss.replace(/\/\*[\s\S]*?\*\//g, '');
    for (const block of withoutComments.split('}')) {
      if (block.includes('!important')) {
        const selector = block.split('{')[0];
        expect(selector).toMatch(/input[.\[]|et-button/);
      }
    }
  });
});
