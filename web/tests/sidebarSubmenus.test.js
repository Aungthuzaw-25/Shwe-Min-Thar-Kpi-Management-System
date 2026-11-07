import { describe, expect, it } from 'vitest';
import { readFileSync, existsSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const submenusSrc = read('static/js/sidebar-submenus.js');

/* Stub DOM: elements keep classes, attributes, dataset, listeners and a
   parent chain (for closest). document.querySelectorAll understands the
   two selectors the script uses. */
function makeStub({ collapsed = false, innerWidth = 1280, location = null } = {}) {
  class StubEl {
    constructor(tag) {
      this.tagName = tag.toUpperCase();
      this._classes = new Set();
      this.children = [];
      this.parentNode = null;
      this.listeners = {};
      this.attributes = {};
      this.dataset = {};
      this.hidden = false;
      const self = this;
      this.classList = {
        add(c) { self._classes.add(c); },
        remove(c) { self._classes.delete(c); },
        contains(c) { return self._classes.has(c); },
      };
    }
    appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
    setAttribute(k, v) { this.attributes[k] = String(v); }
    getAttribute(k) { return this.attributes[k] ?? null; }
    addEventListener(type, fn) { (this.listeners[type] ??= []).push(fn); }
    fire(type, event = {}) { (this.listeners[type] || []).forEach((fn) => fn(event)); }
    closest(cls) {
      let node = this;
      while (node) {
        if (node._classes && node._classes.has(cls.replace(/^\./, ''))) return node;
        node = node.parentNode;
      }
      return null;
    }
    walk(fn) { fn(this); this.children.forEach((child) => child.walk(fn)); }
  }
  const camelize = (s) => s.replace(/-([a-z])/g, (_, c) => c.toUpperCase());
  const matches = (node, sel) => {
    const attr = sel.match(/^([a-zA-Z]+)?\[data-([a-z-]+)\]$/);
    if (attr) {
      if (attr[1] && node.tagName !== attr[1].toUpperCase()) return false;
      return node.dataset[camelize(attr[2])] !== undefined;
    }
    if (sel.startsWith('.')) return node._classes.has(sel.slice(1));
    return node.tagName === sel.toUpperCase();
  };
  const rootEl = new StubEl('div');
  const documentElement = new StubEl('html');
  if (collapsed) documentElement.classList.add('sidebar-collapsed');
  const byId = {};
  const document = {
    StubEl,
    documentElement,
    byId,
    getElementById: (id) => byId[id] || null,
    querySelectorAll: (sel) => {
      const out = [];
      rootEl.walk((node) => { if (matches(node, sel)) out.push(node); });
      return out;
    },
  };
  const store = {};
  const localStorage = {
    getItem: (k) => (k in store ? store[k] : null),
    setItem: (k, v) => { store[k] = String(v); },
  };
  const win = {
    innerWidth,
    location: location || { pathname: '/superadmin/dashboard', search: '', hash: '' },
  };
  return { StubEl, rootEl, document, documentElement, byId, localStorage, store, win };
}

function buildSidebar(stub, { openParents = [] } = {}) {
  const { StubEl, rootEl, byId } = stub;
  const nav = new StubEl('nav');
  rootEl.appendChild(nav);
  const toggle = new StubEl('button');
  toggle.id = 'sidebarCollapseBtn';
  byId.sidebarCollapseBtn = toggle;
  rootEl.appendChild(toggle);
  const parents = {};
  const submenus = {};
  const defs = [
    ['users', ['/superadmin/users?view=active', '/superadmin/users?view=deleted']],
    ['permissions', ['/superadmin/permissions?view=roles', '/superadmin/permissions?view=users']],
  ];
  for (const [name, hrefs] of defs) {
    const parent = new StubEl('a');
    parent.dataset.sidebarSubmenu = name;
    parent.setAttribute('href', '/superadmin/' + name);
    nav.appendChild(parent);
    const submenu = new StubEl('div');
    submenu.id = 'sidebar-submenu-' + name;
    submenu._classes.add('sidebar-submenu');
    submenu.hidden = !openParents.includes(name);
    byId[submenu.id] = submenu;
    nav.appendChild(submenu);
    for (const href of hrefs) {
      const row = new StubEl('a');
      row._classes.add('sidebar-submenu__row');
      row.setAttribute('href', href);
      submenu.appendChild(row);
    }
    parents[name] = parent;
    submenus[name] = submenu;
  }
  return { nav, toggle, parents, submenus };
}

function loadScript(stub) {
  const ctx = vm.createContext({
    window: stub.win, document: stub.document, localStorage: stub.localStorage, console,
  });
  vm.runInContext(submenusSrc, ctx, { filename: 'sidebar-submenus.js' });
}

function clickEvent() {
  let prevented = false;
  return {
    event: {
      button: 0, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
      preventDefault() { prevented = true; },
    },
    get prevented() { return prevented; },
  };
}

describe('inline sidebar submenus', () => {
  it('toggles inline below the parent and closes the other section (accordion)', () => {
    const stub = makeStub();
    const { parents, submenus } = buildSidebar(stub);
    loadScript(stub);
    const usersClick = clickEvent();
    parents.users.fire('click', usersClick.event);
    expect(usersClick.prevented).toBe(true);
    expect(submenus.users.hidden).toBe(false);
    expect(parents.users.getAttribute('aria-expanded')).toBe('true');
    const permClick = clickEvent();
    parents.permissions.fire('click', permClick.event);
    expect(submenus.permissions.hidden).toBe(false);
    expect(submenus.users.hidden).toBe(true);
    expect(parents.users.getAttribute('aria-expanded')).toBe('false');
    parents.permissions.fire('click', clickEvent().event);
    expect(submenus.permissions.hidden).toBe(true);
  });

  it('keeps a server-expanded section open until another parent opens', () => {
    const stub = makeStub();
    const { parents, submenus } = buildSidebar(stub, { openParents: ['users'] });
    loadScript(stub);
    expect(parents.users.getAttribute('aria-expanded')).toBe('true');
    parents.permissions.fire('click', clickEvent().event);
    expect(submenus.users.hidden).toBe(true);
    expect(submenus.permissions.hidden).toBe(false);
  });

  it('on a collapsed rail, a parent click expands the rail AND opens inline', () => {
    const stub = makeStub({ collapsed: true, innerWidth: 1280 });
    const { parents, submenus, toggle } = buildSidebar(stub);
    loadScript(stub);
    parents.users.fire('click', clickEvent().event);
    expect(stub.documentElement.classList.contains('sidebar-collapsed')).toBe(false);
    expect(stub.store['kpi-sidebar-collapsed']).toBe('0');
    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.getAttribute('aria-label')).toBe('Collapse sidebar');
    expect(submenus.users.hidden).toBe(false);
  });

  it('on a collapsed rail, an already-open section stays open (it looks closed in the rail)', () => {
    const stub = makeStub({ collapsed: true, innerWidth: 1280 });
    const { parents, submenus } = buildSidebar(stub, { openParents: ['users'] });
    loadScript(stub);
    parents.users.fire('click', clickEvent().event);
    expect(stub.documentElement.classList.contains('sidebar-collapsed')).toBe(false);
    expect(submenus.users.hidden).toBe(false);
    expect(parents.users.getAttribute('aria-expanded')).toBe('true');
  });

  it('on mobile widths the rail rule does not apply but the section still opens', () => {
    const stub = makeStub({ collapsed: true, innerWidth: 390 });
    const { parents, submenus } = buildSidebar(stub);
    loadScript(stub);
    parents.users.fire('click', clickEvent().event);
    expect(stub.documentElement.classList.contains('sidebar-collapsed')).toBe(true);
    expect(submenus.users.hidden).toBe(false);
  });

  it('passes modified clicks through to the parent href', () => {
    const stub = makeStub();
    const { parents, submenus } = buildSidebar(stub);
    loadScript(stub);
    const evt = clickEvent();
    evt.event.ctrlKey = true;
    parents.users.fire('click', evt.event);
    expect(evt.prevented).toBe(false);
    expect(submenus.users.hidden).toBe(true);
  });

  it('highlights the active child by exact URL, and the Users default tab', () => {
    const deleted = makeStub({ location: { pathname: '/superadmin/users', search: '?view=deleted', hash: '' } });
    const d = buildSidebar(deleted);
    loadScript(deleted);
    const deletedRow = d.submenus.users.children[1];
    expect(deletedRow._classes.has('sidebar-submenu__row--active')).toBe(true);

    const plain = makeStub({ location: { pathname: '/superadmin/users', search: '', hash: '' } });
    const p = buildSidebar(plain);
    loadScript(plain);
    expect(p.submenus.users.children[0]._classes.has('sidebar-submenu__row--active')).toBe(true);
  });

  it('opens a closed section when its child is the active URL', () => {
    const stub = makeStub({ location: { pathname: '/superadmin/users', search: '?view=deleted', hash: '' } });
    const { parents, submenus } = buildSidebar(stub);
    loadScript(stub);
    expect(submenus.users.hidden).toBe(false);
    expect(parents.users.getAttribute('aria-expanded')).toBe('true');
    expect(submenus.users.children[1]._classes.has('sidebar-submenu__row--active')).toBe(true);
  });
});

describe('inline sidebar wiring (template scope)', () => {
  it('replaces every floating panel with an inline section in the admin layout', () => {
    const base = read('templates/layouts/base.njk');
    expect(base).not.toContain('sidebar-panel');
    expect(base).not.toContain('sidebar-panels');
    expect(base).toContain('<link rel="stylesheet" href="/static/css/sidebar-submenus.css">');
    expect(base).toContain('<script src="/static/js/sidebar-submenus.js" defer></script>');
    for (const name of ['dashboard', 'users', 'departments', 'permissions', 'kpis', 'kpi_periods', 'system_settings']) {
      expect(base).toContain(`data-sidebar-submenu="${name}"`);
      expect(base).toContain(`id="sidebar-submenu-${name}"`);
      expect(base).toContain(`aria-controls="sidebar-submenu-${name}"`);
    }
    // Server-rendered auto-expand: the Users section is open on Users pages.
    expect(base).toContain(`{% if active_page != 'users' %} hidden{% endif %}`);
    expect(base).toContain(`{% if active_page == 'users' %}true{% else %}false{% endif %}`);
    // Dashboard counts its child pages as its own section.
    expect(base).toContain(`active_page == 'employee_kpi_results' or active_page == 'department_kpi'`);
    // Child destinations are the submenu-views rows (?view=, from the
    // Users/Departments/Permissions/KPIs/Settings splits on main).
    expect(base).toContain('/superadmin/users?view=active');
    expect(base).toContain('/superadmin/users?view=deleted');
    expect(base).toContain('/superadmin/departments?view=active');
    expect(base).toContain('/superadmin/departments?view=deleted');
    expect(base).toContain('/superadmin/permissions?view=roles');
    expect(base).toContain('/superadmin/permissions?view=users');
    expect(base).toContain('/superadmin/permissions?view=audit');
    expect(base).toContain('/superadmin/kpi?view=records');
    expect(base).toContain('/superadmin/kpi?view=settings');
    expect(base).toContain('/superadmin/kpi?view=assignments');
    expect(base).toContain('/kpi-periods#evaluationPeriodsPanel');
    expect(base).toContain('/kpi-periods#manualEnginePanel');
    expect(base).toContain('/system-settings?view=general');
    expect(base).toContain('/system-settings?view=email');
    expect(base).toContain('/system-settings?view=security');
    expect(base).toContain('/system-settings?view=maintenance');
    expect(base).toContain('/system-settings?view=user-security');
    expect(base).not.toContain('?tab=');
    expect(base).toContain('/superadmin/employee-kpi-results');
  });

  it('deletes the floating panel files and leaves the employee layout untouched', () => {
    expect(existsSync(path.join(root, 'static/js/sidebar-panels.js'))).toBe(false);
    expect(existsSync(path.join(root, 'static/css/sidebar-panels.css'))).toBe(false);
    const employee = read('templates/layouts/employee_base.njk');
    expect(employee).not.toContain('sidebar-submenu');
    expect(employee).not.toContain('sidebar-panel');
  });

  it('styles the inline language: indent rule, rows, active row, rotating chevron', () => {
    const css = read('static/css/sidebar-submenus.css');
    expect(css).toContain('.sidebar-submenu {');
    expect(css).toContain('border-left:');
    expect(css).toContain('.sidebar-submenu__row--active');
    expect(css).toContain('a[aria-expanded="true"] .sidebar-submenu__chev');
    expect(css).toContain('transform: rotate(180deg)');
    expect(css).toContain('html.sidebar-collapsed .app-shell #mainSidebar nav a .sidebar-submenu__chev');
  });
});
