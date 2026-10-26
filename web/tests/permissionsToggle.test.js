import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const read = (rel) => readFileSync(path.join(root, rel), 'utf8');
const template = read('templates/dashboard/permissions.njk');
const css = read('static/css/permissions.css');

describe('permissions toggle (option B)', () => {
  it('renders one toggle per user-matrix row showing the effective state', () => {
    expect(template).toContain('class="perm-toggle');
    expect(template).toContain('role="switch"');
    expect(template).toContain('aria-checked="${p.allowed}"');
    expect(template).toContain('perm-toggle-knob');
    expect(template).toContain('<th class="px-4 py-2 text-center w-48">Allowed</th>');
    expect(template).not.toContain('override-btn');
  });

  it('maps OFF->ON through the role and never guesses inherit when masked', () => {
    expect(template).toContain('row.dataset.pending = row.dataset.roleAllowed === "true" ? "inherit" : "allow"');
    expect(template).toContain('row.dataset.pending = "deny"');
    expect(template).toContain('data-role-allowed');
    expect(template).toContain('data-pending');
  });

  it('keeps the source mark and the effective status on every row', () => {
    for (const label of ['Role', 'Allow Override', 'Deny Override', 'Default']) {
      expect(template).toContain(label);
    }
    expect(template).toContain('perm-source-dot');
    expect(template).toContain('perm-source-label');
    expect(template).toContain('perm-status');
    expect(template).toContain('function refreshUserRow');
  });

  it('lets scope change only while ON and converts a moved scope to an override', () => {
    expect(template).toContain('scope.disabled = !on');
    expect(template).toContain('row.dataset.pending = "allow"');
    expect(template).toContain('data-original-scope');
    expect(template).toContain('data-original-source');
  });

  it('collects only pending allow/deny rows for the unchanged bulk API', () => {
    expect(template).toContain('if (pending !== "allow" && pending !== "deny") return;');
    expect(template).toContain('/api/v1/permissions/user/${uid}/bulk');
    expect(template).toContain('permission_code: row.dataset.code');
  });

  it('keeps Reset and rewords the save confirm without "Inherit"', () => {
    expect(template).toContain('id="resetUserOverrides"');
    expect(template).toContain('Reset to Role Defaults');
    expect(template).toContain('keep following their role');
    expect(template).not.toContain('Permissions set to "Inherit" will use role defaults');
  });

  it('restyles the scope picker as a pill dropdown without changing behaviour', () => {
    expect(css).toContain('.permissions-page .scope-select');
    expect(css).toContain('appearance: none');
    expect(css).toContain('border-radius: 999px');
    expect(css).toContain('data:image/svg+xml');
    expect(css).toContain('.permissions-page .scope-select:focus');
    expect(css).toContain('border-color: #1a1a1a');
    expect(css).toContain('.permissions-page .scope-select:disabled');
    // Same element, same values, same ON-only rule as before.
    expect(template).toContain('class="scope-select');
    expect(template).toContain('<option value="ALL"');
    expect(template).toContain('<option value="DEPARTMENT"');
    expect(template).toContain('<option value="OWN"');
    expect(template).toContain('All Data');
    expect(template).toContain('Department');
    expect(template).toContain('Own Data');
    expect(template).toContain('scope.disabled = !on');
  });

  it('styles the toggle as a dark pill with a white knob and no reduced-motion animation', () => {
    expect(css).toContain('.permissions-page .perm-toggle');
    expect(css).toContain('.permissions-page .perm-toggle.is-on');
    expect(css).toContain('background: #1a1a1a');
    expect(css).toContain('.permissions-page .perm-toggle-knob');
    expect(css).toContain('background: #fff');
    expect(css).toContain('prefers-reduced-motion');
    expect(css).not.toContain('.override-btn');
  });
});
