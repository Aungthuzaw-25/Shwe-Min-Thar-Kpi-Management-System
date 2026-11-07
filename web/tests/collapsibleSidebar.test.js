import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const base = read('templates/layouts/base.njk');
const employee = read('templates/layouts/employee_base.njk');
const css = read('static/css/sidebar-collapse.css');
const js = read('static/js/sidebar-collapse.js');
const submenus = read('static/js/sidebar-submenus.js');

describe('collapsible sidebar', () => {
  it('restores the saved state before first paint in both layouts', () => {
    for (const layout of [base, employee]) {
      expect(layout).toContain("localStorage.getItem('kpi-sidebar-collapsed')");
      expect(layout).toContain("document.documentElement.classList.add('sidebar-collapsed')");
      // Early script sits in <head>, before the stylesheets load.
      expect(layout.indexOf('kpi-sidebar-collapsed')).toBeLessThan(layout.indexOf('stylesheet'));
    }
  });

  it('loads the collapse stylesheet and script in both layouts', () => {
    for (const layout of [base, employee]) {
      expect(layout).toContain('<link rel="stylesheet" href="/static/css/sidebar-collapse.css">');
      expect(layout).toContain('<script src="/static/js/sidebar-collapse.js" defer></script>');
    }
  });

  it('has a collapse toggle reachable in both states, in both layouts', () => {
    for (const layout of [base, employee]) {
      expect(layout).toContain('id="sidebarCollapseBtn"');
      expect(layout).toContain('aria-label="Collapse sidebar"');
      expect(layout).toContain('aria-expanded="true"');
      expect(layout).toContain('sidebar-collapse-btn__icon--collapse');
      expect(layout).toContain('sidebar-collapse-btn__icon--expand');
      // Desktop-only button: the mobile close button keeps its own job.
      expect(layout).toContain('sidebar-collapse-btn hidden md:flex');
    }
  });

  it('persists with one key and toggles one class', () => {
    expect(js).toContain("STORAGE_KEY = 'kpi-sidebar-collapsed'");
    expect(js).toContain("localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0')");
    expect(js).toContain("classList.toggle('sidebar-collapsed', collapsed)");
    expect(js).toContain("aria-label', collapsed ? 'Expand sidebar' : 'Collapse sidebar'");
  });

  it('toggles on Ctrl/Cmd+B but never on Shift (browser bookmarks) or while typing', () => {
    expect(js).toContain('event.ctrlKey || event.metaKey');
    expect(js).toContain('event.shiftKey');
    expect(js).toContain("event.key !== 'b' && event.key !== 'B'");
    expect(js).toContain('event.preventDefault()');
    expect(js).toContain("target.tagName === 'INPUT'");
    expect(js).toContain('target.isContentEditable');
  });

  it('shows body-appended tooltips on collapsed icons (desktop only)', () => {
    expect(js).toContain("tooltip.className = 'sidebar-tooltip'");
    expect(js).toContain('document.body.appendChild(tooltip)');
    expect(js).toContain("link.getBoundingClientRect()");
    expect(js).toContain('window.innerWidth < 768');
    expect(css).toContain('.sidebar-tooltip');
    expect(css).toContain('position: fixed');
  });

  it('collapses to an icon rail on desktop only, keeping today\'s expanded width', () => {
    expect(css).toContain('@media (min-width: 768px)');
    expect(css).toContain('html.sidebar-collapsed .app-shell #mainSidebar');
    expect(css).toContain('width: 4.5rem;');
    expect(css).toContain('font-size: 0;');
    expect(css).toContain('.sidebar-submenu__chev');
    // No collapsed-width rule outside the desktop media block.
    expect(css.split('@media (min-width: 768px)')[0]).not.toContain('4.5rem');
  });

  it('expands the rail AND the submenu on a collapsed parent click (no floating panel)', () => {
    // The submenu script owns the collapsed-click behavior: it lifts the
    // rail itself and opens the section inline. No panel positioning code
    // survives anywhere in the collapse stylesheet.
    expect(submenus).toContain("classList.contains('sidebar-collapsed')");
    expect(submenus).toContain("classList.remove('sidebar-collapsed')");
    expect(submenus).toContain("localStorage.setItem(STORAGE_KEY, '0')");
    expect(submenus).not.toContain('appendChild(panel)');
    expect(css).not.toContain('sidebar-panel__row');
    expect(css).not.toContain('.sidebar-panel {');
  });

  it('keeps the logo mark and avatar visible when collapsed', () => {
    expect(css).toContain('> div:first-child > div > div.min-w-0');
    expect(css).toContain('> div:last-child p');
    expect(css).not.toContain('> div:first-child div.min-w-0 {');
    expect(css).not.toContain('> div:last-child div.min-w-0 {');
  });

  it('keeps the mobile offcanvas behavior (backdrop + close) in both layouts', () => {
    for (const layout of [base, employee]) {
      expect(layout).toContain('id="sidebarBackdrop"');
      expect(layout).toContain('aria-label="Close mobile sidebar"');
      expect(layout).toContain('toggleMobileSidebar()');
    }
    expect(employee).toContain('backdrop.classList.toggle("hidden", !isOpen)');
  });

  it('does not change menu items, order, gating or role colors', () => {
    expect(base).toContain("{% if user_role in ['Super Admin', 'Admin'] %}");
    expect(base).toContain('theme-sa');
    expect(base).toContain('theme-admin');
    expect(css).not.toContain('theme-');
    // The only pre-media sidebar rule is the width/transform transition.
    expect(css.split('@media')[0]).not.toContain('4.5rem');
  });
});
