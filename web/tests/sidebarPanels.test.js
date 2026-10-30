import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const base = read('templates/layouts/base.njk');
const css = read('static/css/sidebar-panels.css');
const js = read('static/js/sidebar-panels.js');
const users = read('templates/dashboard/users.njk');
const departments = read('templates/dashboard/departments.njk');
const permissions = read('templates/dashboard/permissions.njk');
const kpis = read('static/js/superadmin-kpi.js');
const settings = read('templates/dashboard/system_settings.njk');
const periods = read('templates/dashboard/kpi_periods.njk');

describe('Sidebar submenu panels', () => {
  it('gives all 7 parents a panel, and no other link one', () => {
    const parents = base.match(/data-sidebar-panel="[^"]+"/g) || [];
    expect(parents).toHaveLength(7);
    for (const id of ['dashboard', 'users', 'departments', 'permissions', 'kpis', 'kpi_periods', 'system_settings']) {
      expect(base).toContain(`data-sidebar-panel="${id}"`);
      expect(base).toContain(`id="sidebar-panel-${id}"`);
    }
    // KPI Import stays a plain sibling link; Positions has no panel.
    expect(base).toContain('href="/superadmin/kpi-upload"');
    const importLine = base.split('\n').find((line) => line.includes('/superadmin/kpi-upload'));
    expect(importLine).not.toContain('data-sidebar-panel');
    const positionsLine = base.split('\n').find((line) => line.includes('href="/positions"'));
    expect(positionsLine).not.toContain('data-sidebar-panel');
    // Dashboard inline rows are gone; its children live in the panel.
    expect(base).not.toContain('pl-11 pr-2 pb-1');
    expect(base).toContain('sidebar-panel__chev');
    expect(base).not.toContain('style="');
  });

  it('lists the decided children with deep links', () => {
    const panel = (id) => {
      const at = base.indexOf(`id="sidebar-panel-${id}"`);
      return base.slice(at, base.indexOf('</div>', at) + 6);
    };
    expect(panel('dashboard')).toContain('/superadmin/department-kpi');
    expect(panel('users')).toContain('/superadmin/users?view=active');
    expect(panel('users')).toContain('/superadmin/users?view=deleted');
    expect(panel('departments')).toContain('/superadmin/departments?tab=active');
    expect(panel('departments')).toContain('/superadmin/departments?tab=deleted');
    expect(panel('departments')).toContain('>Trash<');
    expect(panel('permissions')).toContain('/superadmin/permissions?tab=roles');
    expect(panel('permissions')).toContain('/superadmin/permissions?tab=users');
    expect(panel('permissions')).toContain('/superadmin/permissions?tab=audit');
    expect(panel('kpis')).toContain('/superadmin/kpi?tab=records');
    expect(panel('kpis')).toContain('/superadmin/kpi?tab=settings');
    expect(panel('kpis')).toContain('/superadmin/kpi?tab=assignments');
    expect(panel('kpi_periods')).toContain('/kpi-periods#evaluationPeriodsPanel');
    expect(panel('kpi_periods')).toContain('/kpi-periods#manualEnginePanel');
    expect(panel('system_settings')).toContain('/system-settings?view=general');
    expect(panel('system_settings')).toContain('/system-settings?view=email');
    expect(panel('system_settings')).toContain('/system-settings?view=security');
    expect(panel('system_settings')).toContain('/system-settings?view=maintenance');
    expect(panel('system_settings')).toContain('/system-settings?view=user-security');
    // The old floating-panel labels are retired with the ?tab= rows.
    expect(panel('system_settings')).not.toContain('?tab=');
    expect(base).toContain('href="/static/css/sidebar-panels.css"');
    expect(base).toContain('src="/static/js/sidebar-panels.js"');
  });

  it('styles the panel as a floating light card, not inline rows', () => {
    expect(css).toContain('.sidebar-panel {');
    expect(css).toContain('position: fixed;');
    expect(css).toContain('background: #ffffff;');
    expect(css).toContain('border-radius: 16px;');
    expect(css).toContain('box-shadow: 0 12px 32px');
    expect(css).toContain('.sidebar-panel[hidden] {');
    expect(css).toContain('.sidebar-panel__row:hover');
    expect(css).toContain('.sidebar-panel-open .sidebar-panel__chev');
  });

  it('toggles on plain left click, closes on Escape and outside click, and follows its parent on scroll and resize', () => {
    expect(js).toContain('data-sidebar-panel');
    expect(js).toContain("event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey");
    expect(js).toContain('event.preventDefault()');
    expect(js).toContain('document.body.appendChild(panel)');
    expect(js).toContain('rect.right + 12');
    expect(js).toContain("event.key === 'Escape'");
    expect(js).toContain("document.addEventListener('scroll'");
    expect(js).toContain("window.addEventListener('resize'");
    expect(js).toContain('followParent');
    expect(js).toContain('rect.bottom < 0 || rect.top > window.innerHeight');
    expect(js).toContain("aria-expanded");
  });

  it('opens the right tab from ?tab= on each tabbed page (Users uses ?view= subpages)', () => {
    // Users split into subpages: ?view= is canonical, legacy ?tab= maps,
    // unknown/missing view defaults to Active — never blank.
    expect(users).toContain("params.get('view')");
    expect(users).toContain("if (view === 'active' || view === 'deleted') return view;");
    expect(users).toContain("if (params.get('tab') === 'deleted') return 'deleted';");
    expect(users).toContain("return 'active';");
    expect(users).toContain("function loadCurrentViewUsers()");
    expect(users).toContain("return USER_VIEW === 'deleted' ? loadDeletedUsers() : loadUsers();");
    expect(departments).toContain("new URLSearchParams(window.location.search).get('tab')");
    expect(departments).toContain("switchTab('deleted')");
    expect(permissions).toContain("['roles', 'users', 'audit'].includes(initialTab)");
    expect(kpis).toContain("const initialPanels = { records: 'records-tab', settings: 'settings-tab', assignments: 'assignments-tab' }");
    // Settings is split into views, not tabs: the ROUTE resolves ?view=
    // (and legacy ?tab= for all 5 values) server-side; the template
    // renders only that view. The old tab-switching code is gone.
    expect(settings).not.toContain('switchSettingTab(initialTab)');
    expect(periods).toContain('id="manualEnginePanel"');
    expect(periods).toContain('id="evaluationPeriodsPanel"');
  });
});
