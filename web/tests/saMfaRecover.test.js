import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

const login = read('templates/auth/login.njk');
const js = read('static/js/sa-mfa-recover.js');

// --- Fake DOM harness (same style as saMfaSetup.test.js) -------------------
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
      const m = /\[data-recover-step="([a-z]+)"\]/.exec(sel);
      if (m && doc.steps[m[1]]) return doc.steps[m[1]];
      return null;
    },
  };
  if (tag === 'div') el.classList.add('hidden');
  return el;
}

function makeEnv(fetchImpl, otpImpl) {
  const doc = {
    activeElement: null, readyState: 'complete',
    byId: {}, steps: {},
    createElement: (tag) => fakeElement(tag, doc),
    getElementById(id) { return this.byId[id] || null; },
  };
  const ids = ['saRecoverEntryBtn', 'saRecoverCloseXBtn', 'saRecoverEmail',
    'saRecoverOtpBoxes', 'saRecoverError', 'saRecoverSubmitBtn', 'saRecoverCancelBtn',
    'saRecoverNewPassword', 'saRecoverConfirmPassword', 'saRecoverResetError',
    'saRecoverResetBtn', 'saRecoverResetCancelBtn', 'saRecoverDoneBtn'];
  for (const id of ids) doc.byId[id] = fakeElement(id.includes('Email') || id.includes('Password') ? 'input' : 'button', doc);
  const modal = fakeElement('div', doc);
  modal.classList.add('hidden');
  for (const name of ['code', 'reset', 'done']) {
    const step = fakeElement('div', doc);
    doc.steps[name] = step;
    if (name !== 'code') step.classList.add('hidden');
    else step.classList.remove('hidden');
  }
  const sandbox = {
    window: {
      OtpInput: otpImpl,
      SharedDialog: { open() {}, close() {} },
      readServerError: async (response) => `Error: server-${response.status}`,
    },
    document: {
      ...doc,
      getElementById(id) {
        if (id === 'saRecoverModal') return modal;
        return doc.getElementById(id);
      },
    },
    fetch: fetchImpl,
    console,
  };
  vm.runInNewContext(js, sandbox);
  return { doc, modal, byId: doc.byId, steps: doc.steps };
}

function fakeOtp() {
  let value = '';
  return {
    set(v) { value = v; },
    clear() { value = ''; },
    focus() {},
    getValue() { return value; },
    get value() { return value; },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(times = 6) {
  for (let i = 0; i < times; i++) await tick();
}

function openModal(env) {
  env.byId.saRecoverEntryBtn.click();
}

// --- Template invariants ----------------------------------------------------
describe('SA recover entry on the login page', () => {
  it('is a pure addition: no existing login line removed, onclick count unchanged', () => {
    expect(login).toContain('id="saRecoverEntryBtn"');
    expect(login).toContain('id="saRecoverModal"');
    expect(login).toContain('Recover with Authenticator');
    expect((login.match(/onclick=/g) || []).length).toBe(4);
  });

  it('adds no inline handlers and no trust-device option', () => {
    const modal = login.slice(login.indexOf('id="saRecoverModal"'), login.indexOf('<!-- Scripts -->'));
    expect(modal).not.toMatch(/onclick=|onchange=|onsubmit=/);
    expect(login).not.toMatch(/trust this device|remember this device|remember-device|trusted device/i);
    expect(js).not.toMatch(/trustDevice|trustedDevice|rememberDevice|trustThisDevice/i);
  });

  it('keeps the existing login flow intact', () => {
    expect(login).toContain('id="loginSubmitBtn"');
    expect(login).toContain('id="otpBoxes"');
    expect(login).toContain('id="forgotPasswordModal"');
    expect(login).toContain('PURGE_PRIVATE');
    expect(login).toContain('id="remember-me"');
    expect(login).toContain('/static/js/sa-mfa-recover.js');
  });

  it('leaves the Forgot Password modal behaviour alone', () => {
    expect(login).toContain('id="resetEmail"');
    expect(login).toContain('Send Reset Link');
    expect(login).toContain('onclick="openForgotPasswordModal()"');
    expect(login).toContain('onclick="closeForgotPasswordModal()"');
    // The recover modal is a separate element with its own ids.
    expect(login).toContain('id="forgotPasswordModal"');
    expect(login).toContain('id="saRecoverModal"');
    expect(login.indexOf('id="saRecoverModal"')).toBeGreaterThan(login.indexOf('id="forgotPasswordModal"'));
  });

  it('calls exactly the live recovery endpoints and never logs or stores', () => {
    expect(js).toContain('/api/v1/auth/sa-mfa/recover');
    expect(js).toContain('/api/v1/auth/sa-mfa/reset-password');
    expect(js).toContain('Invalid email or authenticator code.');
    expect(js).not.toContain('localStorage');
    expect(js).not.toContain('sessionStorage');
    expect(js).not.toMatch(/console\.log/);
    expect(js).not.toMatch(/setInterval\(/);
  });
});

// --- Recover flow ------------------------------------------------------------
describe('SA recover modal behaviour', () => {
  it('wrong code shows ONLY the generic failure and clears the boxes', async () => {
    const otp = fakeOtp();
    const seen = [];
    const fetchImpl = async (url) => {
      seen.push(url);
      return { ok: false, status: 401, json: async () => ({ detail: 'something distinguishing that must not leak' }) };
    };
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    env.byId.saRecoverEmail.value = 'nobody@example.test';
    otp.set('000000');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    expect(env.byId.saRecoverError.textContent).toBe('Invalid email or authenticator code.');
    expect(env.byId.saRecoverError.textContent).not.toContain('distinguishing');
    expect(otp.value).toBe('');
    expect(env.steps.code.classList.contains('hidden')).toBe(false);
    expect(seen).toEqual(['/api/v1/auth/sa-mfa/recover']);
  });

  it('config failure (503) shows the backend plain verification message, not the generic one', async () => {
    const otp = fakeOtp();
    const fetchImpl = async () => ({ ok: false, status: 503, json: async () => ({}) });
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    env.byId.saRecoverEmail.value = 'sa@example.test';
    otp.set('123456');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    expect(env.byId.saRecoverError.textContent).toBe('Authenticator verification is not available right now.');
    expect(env.steps.code.classList.contains('hidden')).toBe(false);
  });

  it('correct code advances to the reset step and clears the code', async () => {
    const otp = fakeOtp();
    const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({}) });
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    env.byId.saRecoverEmail.value = 'sa@example.test';
    otp.set('123456');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    expect(env.steps.reset.classList.contains('hidden')).toBe(false);
    expect(env.steps.code.classList.contains('hidden')).toBe(true);
    expect(otp.value).toBe('');
  });

  it('mismatched passwords are caught client-side without a fetch', async () => {
    const otp = fakeOtp();
    const seen = [];
    const fetchImpl = async (url) => {
      seen.push(url);
      return { ok: true, status: 200, json: async () => ({}) };
    };
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    env.byId.saRecoverEmail.value = 'sa@example.test';
    otp.set('123456');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    env.byId.saRecoverNewPassword.value = 'NewPassword123';
    env.byId.saRecoverConfirmPassword.value = 'Different123';
    env.byId.saRecoverResetBtn.click();
    await settle();
    expect(env.byId.saRecoverResetError.textContent).toContain('does not match');
    expect(seen).toEqual(['/api/v1/auth/sa-mfa/recover']);
  });

  it('matching passwords reset and show the done step', async () => {
    const otp = fakeOtp();
    const fetchImpl = async (url) => {
      if (url.endsWith('/recover')) return { ok: true, status: 200, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => ({ message: 'Password reset.' }) };
    };
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    otp.set('123456');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    env.byId.saRecoverNewPassword.value = 'BrandNewPass123';
    env.byId.saRecoverConfirmPassword.value = 'BrandNewPass123';
    env.byId.saRecoverResetBtn.click();
    await settle();
    expect(env.steps.done.classList.contains('hidden')).toBe(false);
    expect(env.byId.saRecoverNewPassword.value).toBe('');
    expect(env.byId.saRecoverConfirmPassword.value).toBe('');
  });

  it('closing clears code and passwords but keeps the modal reusable', async () => {
    const otp = fakeOtp();
    const fetchImpl = async () => ({ ok: true, status: 200, json: async () => ({}) });
    const env = makeEnv(fetchImpl, { create: () => otp });
    openModal(env);
    otp.set('123456');
    env.byId.saRecoverSubmitBtn.click();
    await settle();
    env.byId.saRecoverNewPassword.value = 'BrandNewPass123';
    env.byId.saRecoverDoneBtn.click();
    await settle();
    expect(otp.value).toBe('');
    expect(env.byId.saRecoverNewPassword.value).toBe('');
    expect(env.steps.code.classList.contains('hidden')).toBe(false);
    expect(env.modal.classList.contains('hidden')).toBe(true);
  });
});
