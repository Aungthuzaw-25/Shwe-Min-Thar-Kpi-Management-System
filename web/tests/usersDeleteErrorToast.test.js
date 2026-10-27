import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';

const root = path.resolve(import.meta.dirname, '..', '..');
const users = readFileSync(path.join(root, 'templates/dashboard/users.njk'), 'utf8');

function fnBody(name) {
  const start = users.indexOf(`async function ${name}(`);
  const next = users.indexOf('async function ', start + 1);
  const endFn = users.indexOf('\n    function ', start + 1);
  const end = next === -1 ? endFn : Math.min(next, endFn === -1 ? next : endFn);
  return users.slice(start, end);
}

describe('Users delete/restore/permanent-delete error toasts', () => {
  it('reads the error body as text (never res.json) in all three functions', () => {
    expect(users).toContain('async function readServerError(res)');
    expect(users).toContain('await res.text()');
    for (const name of ['deleteUser', 'restoreUser', 'permanentDeleteUser']) {
      const body = fnBody(name);
      expect(body).toContain('readServerError(res)');
      expect(body).not.toContain('res.json()');
    }
  });

  it('shows the server detail for JSON errors and the status otherwise', () => {
    const helper = users.slice(users.indexOf('async function readServerError(res)'), users.indexOf('async function deleteUser('));
    expect(helper).toContain('JSON.parse(text)');
    expect(helper).toContain('Error: ${err.detail}');
    expect(helper).toContain('Server error (${res.status})');
  });

  it('keeps "Network error" only for fetch itself throwing', () => {
    for (const [name, message] of [
      ['deleteUser', 'Network error while deleting user.'],
      ['restoreUser', 'Network error while restoring user.'],
      ['permanentDeleteUser', 'Network error while permanently deleting user.'],
    ]) {
      const body = fnBody(name);
      expect(body).toContain(`catch (err) { showToast("${message}", "error"); }`);
    }
  });

  it('keeps success toasts, auth redirects and confirms unchanged', () => {
    expect(users).toContain('User disabled and moved to trash.');
    expect(users).toContain('User restored and activated successfully.');
    expect(users).toContain('User permanently deleted from database.');
    expect(users).toContain("Are you sure you want to disable and move this user to trash?");
    expect(users).toContain('This will permanently delete the user and cannot be undone.');
  });

  it('leaves the out-of-scope save/reset error paths untouched', () => {
    expect(users).toContain("err.detail || 'Validation failed'");
  });
});
