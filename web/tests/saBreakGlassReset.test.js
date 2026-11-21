import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

const login = read('templates/auth/login.njk');
const js = read('static/js/sa-break-glass-reset.js');

// --- Fake DOM harness (same style as saMfaRecover.test.js) ------------------
function fakeElement(tag, doc) {
  const classes = new Set();
  const el = {
    tag: tag.toUpperCase(), tagName: tag.toUpperCase(), children: [],
    attributes: {}, dataset: {}, disabled: false, value: '', textContent: '',
    hidden: false, parentNode: null, listeners: {},
    classList: {
      add: (...c) => c.forEach((x) => classes.add(x)),
      remove: (...c) => c.forEach((x) => classes.delete(x)),
      toggle: (c, force) => {
        if (force === undefined) { if (classes.has(c)) classes.delete(c); else classes.add(c); }
        else if (force) classes.add(c); else classes.delete(c);
      },
      contains: (c) => classes.has(c),
    },
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    focus() { doc.activeElement = this; },
    click() { (this.listeners.click || []).forEach((fn) => fn({})); },
    querySelector(sel) {
      const m = /\[data-breakglass-step="([a-z]+)"\]/.exec(sel);
      if (m && doc.steps[m[1]]) return doc.steps[m[1]];
      return null;
    },
  };
  if (tag === 'div') el.classList.add('hidden');
  return el;
}

function makeEnv(fetchImpl) {
  const doc = {
    activeElement: null, readyState: 'complete',
    byId: {}, steps: {},
    createElement: (tag) => fakeElement(tag, doc),
    getElementById(id) { return this.byId[id] || null; },
  };
  const ids = ['saBreakGlassEntryBtn', 'saBreakGlassCloseXBtn', 'saBreakGlassEmail',
    'saBreakGlassToken', 'saBreakGlassNewPassword', 'saBreakGlassConfirmPassword',
    'saBreakGlassError', 'saBreakGlassDoneMessage', 'saBreakGlassSubmitBtn',
    'saBreakGlassCancelBtn', 'saBreakGlassDoneBtn'];
  for (const id of ids) doc.byId[id] = fakeElement(id.includes('Email') || id.includes('Token') || id.includes('Password') ? 'input' : 'button', doc);
  const modal = fakeElement('div', doc);
  modal.classList.add('hidden');
  for (const name of ['form', 'done']) {
    const step = fakeElement('div', doc);
    doc.steps[name] = step;
    if (name !== 'form') step.classList.add('hidden');
    else step.classList.remove('hidden');
  }
  const sandbox = {
    window: {
      SharedDialog: { open() {}, close() {} },
      readServerError: async (response) => `Error: server-${response.status}`,
      addEventListener() {},
    },
    document: {
      ...doc,
      getElementById(id) {
        if (id === 'saBreakGlassModal') return modal;
        return doc.getElementById(id);
      },
    },
    fetch: fetchImpl,
    console,
  };
  vm.runInNewContext(js, sandbox);
  return { doc, modal, byId: doc.byId, steps: doc.steps };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(times = 6) {
  for (let i = 0; i < times; i++) await tick();
}

function openModal(env) {
  env.byId.saBreakGlassEntryBtn.click();
}

function fillForm(env, { email = 'sa@example.test', token = 'owner-token-0123456789abcdef', password = 'NewPass123!x', confirm = 'NewPass123!x' } = {}) {
  env.byId.saBreakGlassEmail.value = email;
  env.byId.saBreakGlassToken.value = token;
  env.byId.saBreakGlassNewPassword.value = password;
  env.byId.saBreakGlassConfirmPassword.value = confirm;
}

// --- Template invariants ----------------------------------------------------
describe('SA break-glass reset on the login page', () => {
  it('adds the entry, modal, and script without touching the login flow', () => {
    expect(login).toContain('id="saBreakGlassEntryBtn"');
    expect(login).toContain('id="saBreakGlassModal"');
    expect(login).toContain('Reset your password with the break-glass token');
    expect(login).toContain('/static/js/sa-break-glass-reset.js');
    expect(login).toContain('id="loginSubmitBtn"');
    expect(login).toContain('id="saRecoverModal"');
    expect((login.match(/onclick=/g) || []).length).toBe(4);
  });

  it('keeps the token secret: password-type input, no inline handlers', () => {
    expect(login).toContain('<input type="password" id="saBreakGlassToken"');
    const modal = login.slice(login.indexOf('id="saBreakGlassModal"'), login.indexOf('<!-- Scripts -->'));
    expect(modal).not.toMatch(/onclick=|onchange=|onsubmit=/);
    expect(modal).toContain('Rotate the break-glass token after use.');
  });

  it('calls exactly the live endpoint and never logs or stores the token', () => {
    expect(js).toContain('/api/v1/auth/sa-mfa/break-glass-reset-password');
    expect(js).toContain('Invalid email, break-glass token, or account state.');
    expect(js).not.toContain('localStorage');
    expect(js).not.toContain('sessionStorage');
    expect(js).not.toMatch(/console\.log/);
    expect(js).not.toMatch(/setInterval\(/);
  });
});

// --- Reset flow ---------------------------------------------------------------
describe('SA break-glass reset behaviour', () => {
  it('mismatched passwords are caught client-side without a fetch', async () => {
    const seen = [];
    const fetchImpl = async (url) => {
      seen.push(url);
      return { ok: true, status: 200, json: async () => ({ message: 'Password reset.' }) };
    };
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env, { confirm: 'Different123!x' });
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toContain('does not match');
    expect(seen).toEqual([]);
  });

  it('a weak password is caught client-side without a fetch', async () => {
    const seen = [];
    const fetchImpl = async (url) => {
      seen.push(url);
      return { ok: true, status: 200, json: async () => ({ message: 'Password reset.' }) };
    };
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env, { password: 'short1!', confirm: 'short1!' });
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toContain('at least 12 characters');
    expect(seen).toEqual([]);
    fillForm(env, { password: 'Longpassword123', confirm: 'Longpassword123' });
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toContain('special character');
    expect(seen).toEqual([]);
  });

  it('success shows the server message plus the rotate reminder and clears the token', async () => {
    const seen = [];
    const fetchImpl = async (url, options) => {
      seen.push({ url, body: JSON.parse(options.body) });
      return {
        ok: true, status: 200,
        json: async () => ({ message: 'Password reset. Sign in with your new password. Rotate the break-glass token after use.' }),
      };
    };
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env);
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(seen).toHaveLength(1);
    expect(seen[0].url).toBe('/api/v1/auth/sa-mfa/break-glass-reset-password');
    expect(seen[0].body).toEqual({
      email: 'sa@example.test',
      break_glass_token: 'owner-token-0123456789abcdef',
      new_password: 'NewPass123!x',
      confirm_new_password: 'NewPass123!x',
    });
    expect(env.steps.done.classList.contains('hidden')).toBe(false);
    expect(env.steps.form.classList.contains('hidden')).toBe(true);
    expect(env.byId.saBreakGlassDoneMessage.textContent).toContain('Rotate the break-glass token after use.');
    expect(env.byId.saBreakGlassToken.value).toBe('');
    expect(env.byId.saBreakGlassNewPassword.value).toBe('');
    expect(env.byId.saBreakGlassConfirmPassword.value).toBe('');
  });

  it('a bad token shows ONLY the generic failure and clears the token', async () => {
    const fetchImpl = async () => ({ ok: false, status: 401, json: async () => ({ detail: 'something distinguishing that must not leak' }) });
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env, { token: 'wrong-token' });
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toBe('Invalid email, break-glass token, or account state.');
    expect(env.byId.saBreakGlassError.textContent).not.toContain('distinguishing');
    expect(env.byId.saBreakGlassToken.value).toBe('');
    expect(env.steps.form.classList.contains('hidden')).toBe(false);
  });

  it('a 400 shows the server reason and keeps the token', async () => {
    const fetchImpl = async () => ({
      ok: false, status: 400,
      json: async () => ({ detail: 'You cannot reuse any of your last 5 passwords. Please choose a new password.' }),
      text: async () => JSON.stringify({ detail: 'You cannot reuse any of your last 5 passwords. Please choose a new password.' }),
    });
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env);
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toBe('Error: server-400');
    expect(env.byId.saBreakGlassToken.value).toBe('owner-token-0123456789abcdef');
    expect(env.steps.form.classList.contains('hidden')).toBe(false);
  });

  it('config failure (503) shows the plain unavailable message', async () => {
    const fetchImpl = async () => ({ ok: false, status: 503, json: async () => ({}) });
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env);
    env.byId.saBreakGlassSubmitBtn.click();
    await settle();
    expect(env.byId.saBreakGlassError.textContent).toBe('Authenticator break-glass recovery is not available right now.');
    expect(env.steps.form.classList.contains('hidden')).toBe(false);
  });

  it('closing clears the token and passwords but keeps the email', async () => {
    const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({ message: 'Password reset.' }) });
    const env = makeEnv(fetchImpl);
    openModal(env);
    fillForm(env);
    env.byId.saBreakGlassCancelBtn.click();
    await settle();
    expect(env.byId.saBreakGlassToken.value).toBe('');
    expect(env.byId.saBreakGlassNewPassword.value).toBe('');
    expect(env.byId.saBreakGlassConfirmPassword.value).toBe('');
    expect(env.byId.saBreakGlassEmail.value).toBe('sa@example.test');
    expect(env.modal.classList.contains('hidden')).toBe(true);
  });
});
