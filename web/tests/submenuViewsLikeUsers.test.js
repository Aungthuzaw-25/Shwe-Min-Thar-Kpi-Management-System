import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const base = read('templates/layouts/base.njk');
const departments = read('templates/dashboard/departments.njk');
const permissions = read('templates/dashboard/permissions.njk');
const kpiPage = read('templates/dashboard/superadmin_kpi.njk');
const kpiJs = read('static/js/superadmin-kpi.js');

// Extract one named function (brace-matched) from its file and run it
// in a sandbox with a fake location/history, so the test exercises the
// REAL canonicalizer code instead of a copy of it.
function extractFunction(source, signature) {
  const start = source.indexOf(signature);
  if (start < 0) throw new Error(`missing ${signature}`);
  const brace = source.indexOf('{', start);
  let depth = 0;
  for (let i = brace; i < source.length; i++) {
    if (source[i] === '{') depth++;
    else if (source[i] === '}') {
      depth--;
      if (depth === 0) return source.slice(start, i + 1);
    }
  }
  throw new Error(`unbalanced braces in ${signature}`);
}

function runCanonicalizer(fnSource, call, search, globals = {}) {
  const calls = [];
  const sandbox = {
    window: { location: { search } },
    history: { replaceState: (_state, _title, url) => calls.push(url) },
    URLSearchParams,
    ...globals,
  };
  vm.createContext(sandbox);
  vm.runInContext(`${fnSource}\n${call}`, sandbox);
  return calls;
}

describe('submenu views like Users (departments, permissions, KPIs)', () => {
  it('no sidebar child still uses the legacy ?tab= form', () => {
    expect(base).not.toContain('?tab=');
  });

  it('departments: tab bar gone, Add button Active-only, per-view heading', () => {
    expect(departments).not.toContain('id="tabActive"');
    expect(departments).not.toContain('id="tabDeleted"');
    expect(departments).toContain('id="deptViewTitle"');
    expect(departments).toContain('id="deptViewSubtitle"');
    expect(departments).toContain('id="deptAddWrap"');
    expect(departments).toContain("if (deptView === 'deleted') document.getElementById('deptAddWrap')?.remove();");
    // One loader per view: the single fetch reads the resolved view,
    // so a late fetch cannot render the other view's list.
    expect(departments).toContain("if (deptView !== 'active') params.set('is_active', 'false');");
    // Canonicalizer preserves unrelated params (sets view, drops tab).
    expect(departments).toContain("params.set('view', deptView);");
    expect(departments).toContain("params.delete('tab');");
    expect(departments).toContain("history.replaceState(null, '', '/superadmin/departments?' + params.toString());");
    // Sidebar highlight matches the Users pattern (exact href match).
    expect(departments).toContain("row.getAttribute('href') === '/superadmin/departments?view=' + deptView");
  });

  it('permissions: tab bar gone, three panels shown one per view, download section kept', () => {
    expect(permissions).not.toContain('workspace-tabs');
    expect(permissions).toContain('id="permViewTitle"');
    expect(permissions).toContain('id="permViewSubtitle"');
    expect(permissions).toContain('id="rolesPanel"');
    expect(permissions).toContain('id="usersPanel"');
    expect(permissions).toContain('id="auditPanel"');
    expect(permissions).toContain("document.getElementById(PERM_VIEWS[key].panel)?.classList.toggle('hidden', key !== PERM_VIEW);");
    expect(permissions).toContain("if (PERM_VIEW === 'audit') loadAudit(true);");
    // Standalone SA-only section, not a view panel: visible on all views.
    expect(permissions).toContain('id="download-permission-section"');
    // Canonicalizer preserves unrelated params (sets view, drops tab).
    expect(permissions).toContain("params.set('view', PERM_VIEW);");
    expect(permissions).toContain("params.delete('tab');");
    expect(permissions).toContain("history.replaceState(null, '', '/superadmin/permissions?' + params.toString());");
  });

  it('KPIs: tab bar gone, four panels, parent link still lands on Formula Workflow', () => {
    expect(kpiPage).not.toContain('workspace-tabs');
    expect(kpiPage).toContain('id="kpiViewTitle"');
    expect(kpiPage).toContain('id="kpiViewSubtitle"');
    for (const panel of ['formulas', 'records', 'settings', 'assignments']) {
      expect(kpiPage).toContain(`id="${panel}-tab"`);
    }
    expect(kpiJs).toContain("const KPI_DEFAULT_VIEW = 'formulas';");
    expect(base).toContain('href="/superadmin/kpi" data-sidebar-panel="kpis"');
    // The three sidebar children are the three non-default views.
    expect(base).toContain('/superadmin/kpi?view=records');
    expect(base).toContain('/superadmin/kpi?view=settings');
    expect(base).toContain('/superadmin/kpi?view=assignments');
  });
});

describe('canonicalizers preserve unrelated query params (?formula= deep link)', () => {
  it('KPIs: ?formula= survives canonicalization, ?tab= is dropped', () => {
    const fn = extractFunction(kpiJs, 'function canonicalizeKpiViewUrl(view)');
    // Bookmarked deep link: no ?view= yet, so the canonicalizer runs.
    const calls = runCanonicalizer(fn, 'canonicalizeKpiViewUrl("formulas");', '?formula=Test%20Formula');
    expect(calls).toHaveLength(1);
    expect(calls[0]).toContain('view=formulas');
    expect(calls[0]).toContain('formula=Test+Formula');
    expect(calls[0]).not.toContain('tab=');
    // Legacy ?tab= landing with a deep link: tab dropped, formula kept.
    const tabCalls = runCanonicalizer(fn, 'canonicalizeKpiViewUrl("records");', '?tab=records&formula=Test%20Formula');
    expect(tabCalls).toHaveLength(1);
    expect(tabCalls[0]).toContain('view=records');
    expect(tabCalls[0]).toContain('formula=Test+Formula');
    expect(tabCalls[0]).not.toContain('tab=');
    // Already canonical: no rewrite at all.
    const cleanCalls = runCanonicalizer(fn, 'canonicalizeKpiViewUrl("records");', '?view=records&formula=Test%20Formula');
    expect(cleanCalls).toHaveLength(0);
  });

  it('departments + permissions: unrelated params survive, ?tab= is dropped', () => {
    const deptFn = extractFunction(departments, 'function canonicalizeDeptViewUrl()');
    const deptCalls = runCanonicalizer(deptFn, 'canonicalizeDeptViewUrl();', '?tab=deleted&foo=bar', { deptView: 'deleted' });
    expect(deptCalls).toHaveLength(1);
    expect(deptCalls[0]).toContain('view=deleted');
    expect(deptCalls[0]).toContain('foo=bar');
    expect(deptCalls[0]).not.toContain('tab=');

    const permFn = extractFunction(permissions, 'function canonicalizePermViewUrl()');
    const permCalls = runCanonicalizer(permFn, 'canonicalizePermViewUrl();', '?tab=audit&x=1', { PERM_VIEW: 'audit' });
    expect(permCalls).toHaveLength(1);
    expect(permCalls[0]).toContain('view=audit');
    expect(permCalls[0]).toContain('x=1');
    expect(permCalls[0]).not.toContain('tab=');
  });
});
