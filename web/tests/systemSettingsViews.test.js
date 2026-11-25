import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');

const template = read('templates/dashboard/system_settings.njk');
const base = read('templates/layouts/base.njk');
const routes = read('web/src/routes/dashboard.js');

const VIEWS = ['general', 'email', 'security', 'maintenance', 'user-security'];

describe('System Settings split views', () => {
  it('renders only the current view server-side, whitelisted, never blank', () => {
    for (const view of VIEWS) {
      expect(template).toContain(`{% if settings_view == '${view}' %}`);
    }
    expect(template).toContain(
      "settings_view if settings_view in ['general', 'email', 'security', 'maintenance', 'user-security'] else 'general'",
    );
  });

  it('has NO in-page tab list and NO tab-switching code left', () => {
    expect(template).not.toContain('switchSettingTab');
    expect(template).not.toContain('setting-tab-btn');
    expect(template).not.toContain("id=\"tab-general\"");
  });

  it('keeps every section heading and the audit box inside Maintenance', () => {
    expect(template).toContain('Company & Localization');
    expect(template).toContain('Configured Email Providers');
    expect(template).toContain('Security & Password Policy');
    expect(template).toContain('System Maintenance');
    expect(template).toContain('User Lockout Settings');
    expect(template).toContain('id="set_audit_retention_years"');
    const maintenanceAt = template.indexOf('id="content-maintenance"');
    const auditAt = template.indexOf('id="set_audit_retention_years"');
    expect(auditAt).toBeGreaterThan(maintenanceAt);
  });

  it('shows Save All on every view except User Settings', () => {
    expect(template).toContain("{% if settings_view != 'user-security' %}");
    expect(template).toContain('id="saveSettingsBtn"');
  });

  it('keeps the trademark block on the General view only (it leaked onto every tab on main)', () => {
    const generalAt = template.indexOf("settings_view == 'general'");
    const trademarkAt = template.indexOf('id="trademarkSection"');
    const emailAt = template.indexOf("settings_view == 'email'");
    expect(trademarkAt).toBeGreaterThan(generalAt);
    expect(trademarkAt).toBeLessThan(emailAt);
    expect(template).toContain('id="tm_logo_dropzone"');
  });

  it('loads only the current view\'s data (no hidden-section fetches)', () => {
    expect(template).toContain("if (SETTINGS_VIEW === 'email')");
    expect(template).toContain("} else if (SETTINGS_VIEW === 'user-security') {");
    expect(template).toContain("if (SETTINGS_VIEW === 'general') loadTrademark();");
    // Canonical URL is replaceState-guarded so it can never block load.
    expect(template).toContain('history.replaceState');
    expect(template).toContain('catch (_) { /* keep the page usable */ }');
  });

  // NOTE (2026-11-06): the FastAPI tier used to resolve ?view=/?tab=
  // too (app/web/dashboard_web.py); Jinja2 page rendering was removed,
  // so the web tier is the only tier now.
  it('resolves ?view= in the web tier, with legacy ?tab= mapping for all 5 values', () => {
    const block = routes.slice(routes.indexOf('"/system-settings"'));
    expect(block).toContain('settings_view');
    expect(block).toContain('resolveSettingsView');
    expect(routes).toContain('"general", "email", "security", "maintenance", "user-security"');
  });

  it('has exactly ONE Company Name field — the Trademark one (no duplicate in Localization)', () => {
    // Zenn 2026-11-14: two fields edited the same stored value
    // (general.company_name) with different values — the Localization
    // field's hardcoded "Enterprise KPI Corp" survived load (the
    // stored name was missing) and Save All would have overwritten
    // the live brand with it.
    const labels = template.match(/>Company Name </g) || [];
    expect(labels).toHaveLength(1);
    expect(template).toContain('id="tm_company_name"');
    expect(template).not.toContain('set_company_name');
    expect(template).not.toContain('Enterprise KPI Corp');
    expect(template).toContain('Company name is set in Company Trademark below');
  });

  it('Save All still preserves the brand name without the removed field', () => {
    // The backend replaces the whole `general` dict, so the last
    // loaded brand name is carried forward — otherwise saving
    // timezone/date format would delete the stored company name and
    // the brand would fall back to the default.
    expect(template).toContain('let loadedCompanyName');
    expect(template).toContain('loadedCompanyName = brand.company_name');
    expect(template).toContain('company_name: loadedCompanyName,');
    expect(template).toContain("document.getElementById('set_timezone')");
  });

  it('gives the sidebar exactly the 5 photo-label children, in photo order', () => {
    const panelStart = base.indexOf('id="sidebar-submenu-system_settings"');
    const panel = base.slice(panelStart, base.indexOf('</div>', panelStart));
    const labels = ['General Info', 'Email Configuration', 'Security Policy', 'Maintenance', 'User Settings'];
    let last = -1;
    for (const label of labels) {
      const at = panel.indexOf(`>${label}<`);
      expect(at).toBeGreaterThan(last);
      last = at;
    }
    expect(panel).toContain('/system-settings?view=user-security');
    expect(panel).not.toContain('Company & Localization');
  });
});
