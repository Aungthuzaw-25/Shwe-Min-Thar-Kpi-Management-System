import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const audit = read('templates/dashboard/audit_logs.njk');

// The label file is pure JS (no DOM, no network), so load the shipped
// file in a vm and test the real mapping — not just the source text.
const sandbox = { window: {}, console };
vm.createContext(sandbox);
vm.runInContext(read('static/js/audit-labels.js'), sandbox);
const L = sandbox.window.AuditLabels;

describe('Audit Logs labels (shipped audit-labels.js)', () => {
  it('labels the actions the backend writes', () => {
    expect(L.actionLabel('SESSION_REVOKED')).toBe('Session revoked');
    expect(L.actionLabel('LOGIN')).toBe('Signed in');
    expect(L.actionLabel('USER_HARD_DELETED')).toBe('User permanently deleted');
    expect(L.actionLabel('UPDATE_KPI_OVERRIDE')).toBe('KPI override updated');
    expect(L.actionLabel('OAUTH_LOGIN_REJECTED')).toBe('OAuth sign-in rejected');
    expect(L.actionLabel('AUDIT_LOG_PURGE')).toBe('Audit logs purged');
    expect(L.actionLabel('BOOTSTRAP_SUPER_ADMIN')).toBe('Super Admin created');
    expect(L.actionLabel('UPDATE')).toBe('Updated');
    expect(L.actionLabel('')).toBe('Unknown');
    expect(L.actionLabel(null)).toBe('Unknown');
  });

  it('humanizes an unknown action instead of showing it raw', () => {
    expect(L.actionLabel('SOME_FUTURE_ACTION')).toBe('Some future action');
    expect(L.actionLabel('KPI_SOMETHING_NEW')).toBe('KPI something new');
  });

  it('labels the tables the backend writes', () => {
    expect(L.tableLabel('auth_sessions')).toBe('Auth sessions');
    expect(L.tableLabel('kpi_formulas')).toBe('KPI formulas');
    expect(L.tableLabel('system_settings')).toBe('System settings');
    expect(L.tableLabel('oauth_accounts')).toBe('OAuth accounts');
    expect(L.tableLabel(null)).toBe('—');
    expect(L.tableLabel('some_new_table')).toBe('Some new table');
  });

  it('labels the reason codes and never shows "None" for a missing reason', () => {
    expect(L.reasonLabel(null)).toBe('No reason given');
    expect(L.reasonLabel('')).toBe('No reason given');
    expect(L.reasonLabel('new_login_same_device')).toBe('New login from the same device');
    expect(L.reasonLabel('user_revoked_session')).toBe('Session revoked by the user');
    expect(L.reasonLabel('device_limit')).toBe('Device limit reached');
    expect(L.reasonLabel('refresh_token_reuse')).toBe('Refresh token reuse detected');
    // Free text (user-entered reasons) passes through untouched.
    expect(L.reasonLabel('late submission')).toBe('late submission');
  });

  it('humanizes only snake_case codes, never free text or numbers', () => {
    expect(L.humanizeCode('KPI_RECORDS')).toBe('KPI records');
    expect(L.humanizeCode('SA_MFA_RESET')).toBe('SA MFA reset');
    expect(L.humanizeCode('free text here')).toBe('free text here');
    expect(L.humanizeCode('500')).toBe('500');
    // labelValue is the generic formatter (no key context): it humanizes
    // the code, while the reason-specific wording ("...from the same
    // device") comes from reasonLabel, which the template applies only
    // for the `reason` key.
    expect(L.labelValue('new_login_same_device')).toBe('New login same device');
    expect(L.labelValue('None')).toBe('None');
    expect(L.labelValue('(empty)')).toBe('(empty)');
  });
});

describe('Audit Logs page format', () => {
  it('uses the labels everywhere a code used to show', () => {
    expect(audit).toContain('<script src="/static/js/audit-labels.js"></script>');
    expect(audit).toContain('auditLabels.actionLabel(log.action)');
    expect(audit).toContain('auditLabels.tableLabel(log.table_name)');
    expect(audit).toContain('auditLabels.actionLabel(action)');
    expect(audit).toContain('formatChangeValue(key, oldObject[key])');
    expect(audit).toContain('formatChangeValue(key, newObject[key])');
    // The pill must not force the label back to upper case.
    expect(audit).not.toContain('font-extrabold uppercase tracking-wide ring-1');
    // The resource label is plain text now, not monospace code.
    expect(audit).not.toContain('<div class="font-mono text-xs font-bold text-slate-700">');
    expect(audit).toContain('<div class="text-xs font-bold text-slate-700">${escapeHTML(auditLabels.tableLabel(log.table_name))}</div>');
  });

  it('drops the redundant "Actor" sub-label (the API returns no role)', () => {
    expect(audit).not.toContain('>Actor</div>');
    expect(audit).toContain('${escapeHTML(log.admin_name || "System Auto")}</div></div></div>');
  });

  it('labels the Source value as an IP address', () => {
    expect(audit).toContain('IP address</div>');
    expect(audit).toContain('${escapeHTML(log.ip_address || "Unknown")}</div>');
  });

  it('lets the page span the full content width (no max-w-7xl cap)', () => {
    expect(audit).not.toContain('max-w-7xl');
    expect(audit).toContain('<div class="mx-auto">');
  });

  it('keeps the search, filter, pagination and row behaviour untouched', () => {
    expect(audit).toContain('id="searchInput"');
    expect(audit).toContain('id="actionFilter"');
    expect(audit).toContain('id="previousPageButton"');
    expect(audit).toContain('id="nextPageButton"');
    expect(audit).toContain('const pageSize = 50;');
    expect(audit).toContain("`/api/v1/kpi/audit-logs?${params}`");
    expect(audit).toContain('actionClass(log.action)');
  });
});
