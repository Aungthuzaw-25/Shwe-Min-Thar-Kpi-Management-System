import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

function fakeElement(tag, doc) {
  const el = {
    tag: tag.toUpperCase(), tagName: tag.toUpperCase(), children: [],
    attributes: {}, dataset: {}, disabled: false, value: '', textContent: '',
    parentNode: null, isConnected: true, listeners: {}, inputMode: '',
    maxLength: 524288, autocomplete: '',
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    focus() { doc.activeElement = this; },
    select() {},
  };
  return el;
}

function makeEnv() {
  const doc = {
    activeElement: null,
    createElement: (tag) => fakeElement(tag, doc),
  };
  const sandbox = { window: {}, document: doc };
  vm.runInNewContext(read('static/js/otp-input.js'), sandbox);
  return { doc, OtpInput: sandbox.window.OtpInput };
}

function makeOtp(doc, OtpInput) {
  const container = fakeElement('div', doc);
  container.ownerDocument = doc;
  const otp = OtpInput.create(container);
  return { container, otp };
}

function typeInto(box, value) {
  box.value = value;
  (box.listeners.input || []).forEach((fn) => fn({}));
}

function pressKey(box, key, extra = {}) {
  let prevented = false;
  (box.listeners.keydown || []).forEach((fn) => fn({
    key, ...extra, preventDefault() { prevented = true; },
  }));
  return prevented;
}

describe('OtpInput component', () => {
  it('auto-advances on input and reports the joined value', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    expect(otp.boxes).toHaveLength(6);
    typeInto(otp.boxes[0], '1');
    expect(doc.activeElement).toBe(otp.boxes[1]);
    typeInto(otp.boxes[1], '2');
    typeInto(otp.boxes[2], '3');
    expect(otp.getValue()).toBe('123');
  });

  it('Backspace on an empty box moves back and clears it; on a filled box clears it', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    typeInto(otp.boxes[0], '1');
    typeInto(otp.boxes[1], '2');
    // Focus is on box 3 (empty). Backspace -> box 2 cleared + focused.
    pressKey(otp.boxes[2], 'Backspace');
    expect(otp.boxes[1].value).toBe('');
    expect(doc.activeElement).toBe(otp.boxes[1]);
    // Backspace on the now-empty box 2 -> box 1 cleared + focused.
    pressKey(otp.boxes[1], 'Backspace');
    expect(otp.boxes[0].value).toBe('');
    expect(doc.activeElement).toBe(otp.boxes[0]);
    // Backspace on a filled box clears it and stays. (Typing advanced
    // focus to box 2, so put focus back on box 1 first, as a real user
    // clicking back into it would.)
    typeInto(otp.boxes[0], '9');
    doc.activeElement = otp.boxes[0];
    pressKey(otp.boxes[0], 'Backspace');
    expect(otp.boxes[0].value).toBe('');
    expect(doc.activeElement).toBe(otp.boxes[0]);
  });

  it('paste fills from the focused box onward and strips non-digits', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    let prevented = false;
    (otp.boxes[1].listeners.paste || []).forEach((fn) => fn({
      clipboardData: { getData: () => '12a34b' },
      preventDefault() { prevented = true; },
    }));
    expect(prevented).toBe(true);
    expect(otp.getValue()).toBe('1234');
    expect(doc.activeElement).toBe(otp.boxes[5]);
  });

  it('strips non-digits on input and never advances on them', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    typeInto(otp.boxes[0], 'x');
    expect(otp.boxes[0].value).toBe('');
    expect(doc.activeElement).toBe(null);
    typeInto(otp.boxes[0], '5');
    expect(doc.activeElement).toBe(otp.boxes[1]);
  });

  it('clear empties every box; first box carries the one-time-code hint and labels', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    typeInto(otp.boxes[0], '1');
    typeInto(otp.boxes[1], '2');
    otp.clear();
    expect(otp.getValue()).toBe('');
    expect(otp.boxes[0].autocomplete).toBe('one-time-code');
    expect(otp.boxes[1].autocomplete).toBe('off');
    expect(otp.boxes[0].inputMode).toBe('numeric');
    expect(otp.boxes[3].attributes['aria-label']).toBe('Digit 4 of 6');
  });

  it('arrow keys move between boxes', () => {
    const { doc, OtpInput } = makeEnv();
    const { otp } = makeOtp(doc, OtpInput);
    doc.activeElement = otp.boxes[2];
    pressKey(otp.boxes[2], 'ArrowLeft');
    expect(doc.activeElement).toBe(otp.boxes[1]);
    pressKey(otp.boxes[1], 'ArrowRight');
    expect(doc.activeElement).toBe(otp.boxes[2]);
  });
});

describe('login OTP wiring pins', () => {
  const login = read('templates/auth/login.njk');
  it('loads the shared component + stylesheet on the login page', () => {
    expect(login).toContain('<script src="/static/js/otp-input.js">');
    expect(login).toContain('<link rel="stylesheet" href="/static/css/otp-input.css">');
    expect(login).toContain('id="otpBoxes"');
    expect(login).toContain('id="otpBackBtn"');
    expect(login).toContain('data-login-step="otp"');
  });

  it('submits password first, then password + totp_code — same URL, no new params', () => {
    expect(login).toContain("fetch('/api/v1/auth/login'");
    expect(login).toContain('if (totpCode) body.totp_code = totpCode;');
    expect(login).toContain("body = { email, password, remember_me: rememberMe }");
    expect(login).not.toContain('totp_code: totpCode,');
  });

  it('handles each MFA error code distinctly', () => {
    expect(login).toContain("errorData.detail?.code === 'MFA_REQUIRED'");
    expect(login).toContain('setLoginStep(true)');
    expect(login).toContain("errorData.detail?.code === 'MFA_INVALID'");
    expect(login).toContain('Invalid authenticator code.');
    expect(login).toContain('otp.clear()');
    expect(login).toContain("errorData.detail?.code === 'MFA_UNAVAILABLE'");
    expect(login).toContain('Authenticator verification is not available right now.');
  });

  it('password step, lockout, and generic errors are unchanged', () => {
    expect(login).toContain("errorData.detail?.code === 'ACCOUNT_LOCKED'");
    expect(login).toContain("'Invalid email or password.'");
    expect(login).toContain('A network error occurred. Please try again.');
    expect(login).toContain('id="loginLockoutAlert"');
    expect(login).toContain('bg-red-800 hover:bg-red-900');
    expect(login).not.toContain("submitBtn.innerText = originalText");
  });

  it('incomplete codes never reach the backend', () => {
    expect(login).toContain('if (totpCode.length !== 6)');
    expect(login).toContain('otp.boxes.find((box) => !box.value)');
  });
});
