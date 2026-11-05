import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

const login = read('templates/auth/login.njk');
const css = read('static/css/notifications.css');
const js = read('static/js/notifications.js');

describe('login toast stylesheet (Zenn photo fix)', () => {
  it('links notifications.css exactly once, right after dialog.css', () => {
    const links = [...login.matchAll(/<link rel="stylesheet" href="([^"]+)">/g)].map((m) => m[1]);
    expect(links.filter((href) => href === '/static/css/notifications.css')).toHaveLength(1);
    expect(links.indexOf('/static/css/notifications.css'))
      .toBe(links.indexOf('/static/css/dialog.css') + 1);
  });

  it('the stylesheet defines every class the toast builder uses', () => {
    for (const cls of ['app-toast-container', 'app-toast', 'app-toast-error',
      'app-toast-icon', 'app-toast-message', 'app-toast-close']) {
      expect(css).toContain(`.${cls}`);
    }
    expect(css).toContain('background: #fff');
    expect(css).toContain('border-left: 4px solid');
    expect(js).toContain('app-toast app-toast-${kind}');
    expect(js).toContain('app-toast-icon');
    expect(js).toContain('app-toast-message');
    expect(js).toContain('app-toast-close');
  });

  it('changes nothing else about the login page wiring', () => {
    expect(login).toContain('id="loginSubmitBtn"');
    expect(login).toContain('id="otpBoxes"');
    expect(login).toContain('id="saRecoverEntryBtn"');
    expect(login).toContain('id="forgotPasswordModal"');
    expect((login.match(/onclick=/g) || []).length).toBe(4);
    expect(login).toContain('showToastLegacy');
  });
});
