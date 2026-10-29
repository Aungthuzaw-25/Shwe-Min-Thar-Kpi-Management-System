import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');

const page = read('templates/dashboard/whats_new_page.njk');
const teaser = read('templates/dashboard/whats_new.njk');
const teaserJs = read('static/js/whats-new-teaser.js');
const pageJs = read('static/js/whats-new-page.js');
const base = read('templates/layouts/base.njk');
const employeeBase = read('templates/layouts/employee_base.njk');
const routes = read('web/src/routes/dashboard.js');
const saDashboard = read('templates/dashboard/superadmin_executive_dashboard.njk');
const executiveContent = read('templates/dashboard/executive_dashboard_content.njk');

function loadPageJs() {
  const sandbox = { document: { getElementById: () => null }, window: {} };
  vm.runInNewContext(pageJs, sandbox);
  // The IIFE early-returns (no #whatsNewPage) but still exposes helpers.
  return sandbox.window.WhatsNewPage;
}

describe("What's new page", () => {
  it('extends the role layout, lists published entries, and gates manage UI on system.settings.edit', () => {
    expect(page).toContain('{% extends layout_template %}');
    expect(page).toContain('data-wn-list');
    expect(page).toContain('data-wn-empty');
    expect(page).toContain("permission_map['system.settings.edit']");
    expect(page).toContain('data-wn-form');
    expect(page).toContain('Publish now');
  });

  it('has NO save-draft button and NO localStorage workaround', () => {
    expect(page.toLowerCase()).not.toContain('save as draft');
    // No actual storage access (the header comment names the ban).
    expect(pageJs).not.toContain('localStorage.');
    expect(pageJs).not.toContain('sessionStorage.');
  });

  it('renders links only for internal paths and images only when present', () => {
    expect(pageJs).toContain("charAt(0) === '/'");
    expect(pageJs).toContain('if (entry.image_path)');
  });

  it('marks NEW only within 14 days of published_at', () => {
    const helpers = loadPageJs();
    const now = Date.now();
    expect(helpers.isNew(new Date(now - 13 * 24 * 3600 * 1000).toISOString())).toBe(true);
    expect(helpers.isNew(new Date(now - 14 * 24 * 3600 * 1000 - 1000).toISOString())).toBe(false);
    expect(helpers.isNew(new Date(now + 3600 * 1000).toISOString())).toBe(false);
    expect(helpers.isNew(null)).toBe(false);
    expect(helpers.linkKind('/tasks')).toBe('link');
    expect(helpers.linkKind('https://example.com/x')).toBe('text');
    expect(helpers.linkKind(null)).toBe('none');
    expect(helpers.formatDate('2026-10-28T12:00:00Z')).toMatch(/\d{2} \w{3} 2026/);
  });

  it('has a top-level route with no role gate (API filters by family)', () => {
    const idx = routes.indexOf('"/whats-new"');
    expect(idx).toBeGreaterThan(-1);
    const block = routes.slice(idx, routes.indexOf('});', idx));
    expect(block).not.toContain('allowedRoles');
    expect(block).toContain('layout_template');
  });

  it('has one sidebar line in each layout, not a submenu child', () => {
    const baseLine = base.split('\n').find((line) => line.includes('href="/whats-new"'));
    expect(baseLine).toBeDefined();
    expect(baseLine).not.toContain('data-sidebar-panel');
    expect(baseLine).toContain("active_page == 'whats_new'");
    const empLine = employeeBase.split('\n').find((line) => line.includes('href="/whats-new"'));
    expect(empLine).toBeDefined();
    expect(empLine).not.toContain('data-sidebar-panel');
    expect(empLine).toContain("active_page == 'whats_new'");
    // Employee entry sits in the shared menu (after Notifications).
    expect(employeeBase.indexOf('href="/whats-new"'))
      .toBeGreaterThan(employeeBase.indexOf('href="/notifications"'));
  });
});

describe("What's new dashboard teaser", () => {
  it('fetches the latest 3 from the API and links to the full page', () => {
    expect(teaser).toContain('href="/whats-new"');
    expect(teaser).toContain('See all');
    expect(teaser).toContain('data-wn-teaser-list');
    expect(teaserJs).toContain('limit=3');
    expect(teaserJs).toContain('/api/v1/system/whats-new/');
  });

  it('contains NO retired static entries (never static + DB at once)', () => {
    for (const item of ['Formula period links', 'Form drafts save automatically',
      'KPI periods by year', 'System logs in plain language',
      'Audit log retention setting', 'Offline mode (read-only)']) {
      expect(teaser).not.toContain(item);
    }
  });

  it('still appears on the SA dashboard and the shared executive content', () => {
    expect(saDashboard).toContain('{% include "dashboard/whats_new.njk" %}');
    expect(executiveContent).toContain('{% include "dashboard/whats_new.njk" %}');
  });
});
