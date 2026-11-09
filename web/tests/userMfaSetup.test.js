import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

const body = read('templates/dashboard/account_profile_body.njk');
const js = read('static/js/user-mfa-setup.js');
const saJs = read('static/js/sa-mfa-setup.js');

// --- Fake DOM harness (same style as saMfaSetup.test.js) ------------------
function fakeElement(tag, doc) {
  const el = {
    tag: tag.toUpperCase(), tagName: tag.toUpperCase(), children: [],
    attributes: {}, dataset: {}, disabled: false, value: '', textContent: '',
    hidden: false, width: 0, height: 0, parentNode: null, listeners: {},
    className: '', style: {},
    setAttribute(k, v) { this.attributes[k] = String(v); },
    getAttribute(k) { return k in this.attributes ? this.attributes[k] : null; },
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    appendChild(child) { child.parentNode = this; this.children.push(child); return child; },
    focus() { doc.activeElement = this; },
    click() { (this.listeners.click || []).forEach((fn) => fn({})); },
    querySelector(sel) { return doc.queryFrom(this, sel); },
    getContext() { return { fillStyle: '', fillRect() {}, clearRect() {} }; },
  };
  return el;
}

function makeEnv(fetchImpl, otpImpl, qrImpl) {
  const doc = {
    activeElement: null, readyState: 'complete', listeners: {},
    elements: {},
    createElement: (tag) => fakeElement(tag, doc),
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); },
    getElementById(id) { return this.elements[id] || null; },
    queryFrom(scope, sel) {
      // Only the selectors the card script uses: [data-user-mfa-*] inside root.
      const m = /\[data-user-mfa-([a-z-]+)\]/.exec(sel);
      if (!m) return null;
      return scope.byData ? scope.byData[m[1]] || null : null;
    },
  };
  const byData = {};
  const root = fakeElement('div', doc);
  root.byData = byData;
  const dataNames = ['status', 'signin', 'start', 'setup', 'enrolled', 'disable',
    'reset', 'start-btn', 'qr', 'qr-fallback', 'key', 'copy', 'otp', 'confirm',
    'cancel', 'error', 'disable-btn', 'disable-otp', 'disable-confirm',
    'disable-cancel', 'disable-error', 'reset-link', 'reset-email', 'reset-btn',
    'reset-cancel', 'reset-error', 'reset-msg', 'pending'];
  for (const name of dataNames) {
    const el = fakeElement(name === 'qr' ? 'canvas' : 'div', doc);
    el.byData = byData;
    byData[name] = el;
  }
  doc.elements.userMfaCard = root;
  const timers = [];
  const sandbox = {
    window: {
      qrcode: qrImpl,
      OtpInput: otpImpl,
      addEventListener() {},
      setTimeout(fn) { timers.push(fn); },
    },
    document: doc,
    navigator: { clipboard: { writeText: async () => {} } },
    fetch: fetchImpl,
    console,
  };
  // The card script reads window.* — mirror them onto the sandbox global.
  sandbox.window.document = doc;
  vm.runInNewContext(js, sandbox);
  return { doc, root, byData, timers, sandbox };
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

function fakeQr() {
  return function (typeNumber, level) {
    void typeNumber; void level;
    return {
      addData() {}, make() {}, getModuleCount() { return 21; },
      isDark() { return false; },
      renderTo2dContext() {},
    };
  };
}

const tick = () => new Promise((r) => setTimeout(r, 0));
async function settle(times = 6) {
  for (let i = 0; i < times; i++) await tick();
}

// --- Template + security invariants ---------------------------------------
describe('Admin/HR MFA card template', () => {
  it('renders the card only for Admin/HR, inside the Security section', () => {
    expect(body).toContain('{% if user_role in ["Admin", "HR"] %}');
    expect(body).toContain('id="userMfaCard"');
    const securityAt = body.indexOf('id="security"');
    const cardAt = body.indexOf('id="userMfaCard"');
    const sessionsAt = body.indexOf('id="acctSessions"');
    const saCardAt = body.indexOf('id="saMfaCard"');
    expect(securityAt).toBeGreaterThan(-1);
    expect(cardAt).toBeGreaterThan(securityAt);
    expect(cardAt).toBeGreaterThan(sessionsAt);
    // The user card sits after the SA card; SA users never see it
    // (their role is "Super Admin", not Admin/HR).
    expect(cardAt).toBeGreaterThan(saCardAt);
  });

  it('gates the OTP, QR-vendor and setup scripts behind the same Admin/HR check', () => {
    const scripts = body.slice(body.indexOf('account-sessions.js'));
    expect(scripts).toContain('{% if user_role in ["Admin", "HR"] %}');
    expect(scripts).toContain('/static/js/otp-input.js');
    expect(scripts).toContain('/static/vendor/qrcode-generator.js');
    expect(scripts).toContain('/static/js/user-mfa-setup.js');
  });

  it('leaves the SA card and its scripts untouched', () => {
    expect(body).toContain('id="saMfaCard"');
    expect(body).toContain('/static/js/sa-mfa-setup.js');
    expect(body).toContain("{% if account_profile.is_super_admin %}");
    // The user script must not call the SA endpoints, and vice versa.
    expect(js).not.toContain('/api/v1/auth/sa-mfa/');
    expect(saJs).not.toContain('/api/v1/auth/user-mfa/');
  });

  it('has no inline handlers in the card and no external QR reference', () => {
    const card = body.slice(body.indexOf('id="userMfaCard"'), body.indexOf('id="userMfaCard"') + 5000);
    expect(card).not.toMatch(/onclick=|onchange=|onsubmit=/);
    expect(body).not.toMatch(/api\.qrserver|qrcode\.techahead|chart\.googleapis|image-charts/);
    // The setup script must contain no absolute URL at all: all API
    // calls are relative /api/ paths and the QR is drawn locally.
    expect(js).not.toMatch(/https?:\/\//);
  });

  it('never stores or logs the secret', () => {
    expect(js).not.toContain('localStorage');
    expect(js).not.toContain('sessionStorage');
    expect(js).not.toMatch(/console\.log\(.*(pending|secret|key|otpauth)/i);
    expect(js).toContain('pagehide');
  });

  it('calls exactly the live Backend endpoints', () => {
    expect(js).toContain('/api/v1/auth/user-mfa/status');
    expect(js).toContain('/api/v1/auth/user-mfa/setup');
    expect(js).toContain('/api/v1/auth/user-mfa/confirm');
    expect(js).toContain('/api/v1/auth/user-mfa/disable');
    expect(js).toContain('/api/v1/auth/user-mfa/reset-requests');
  });
});

// --- Card state machine -----------------------------------------------------
describe('Admin/HR MFA card behaviour', () => {
  it('shows the enrolled state when status says enrolled', async () => {
    const fetchImpl = async () => ({
      ok: true, status: 200,
      json: async () => ({ enrolled: true, setup_required: false, pending_request_kind: null }),
    });
    const { byData } = makeEnv(fetchImpl, { create: () => fakeOtp() }, fakeQr());
    await settle();
    expect(byData.enrolled.hidden).toBe(false);
    expect(byData.start.hidden).toBe(true);
    expect(byData.setup.hidden).toBe(true);
    expect(byData.status.textContent).toContain('Authenticator is on');
    expect(byData.pending.hidden).toBe(true);
  });

  it('shows the pending notice when a reset request is already waiting', async () => {
    const fetchImpl = async () => ({
      ok: true, status: 200,
      json: async () => ({ enrolled: true, setup_required: false, pending_request_kind: 'RESET' }),
    });
    const { byData } = makeEnv(fetchImpl, { create: () => fakeOtp() }, fakeQr());
    await settle();
    expect(byData.enrolled.hidden).toBe(false);
    expect(byData.pending.hidden).toBe(false);
  });

  it('shows start when not enrolled, then setup with QR + key on setup click', async () => {
    const otp = fakeOtp();
    let qrDrawn = false;
    const qrImpl = function () {
      return {
        addData() {}, make() {}, getModuleCount() { return 21; },
        isDark() { return false; },
        renderTo2dContext() { qrDrawn = true; },
      };
    };
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: false, setup_required: true, pending_request_kind: null }) };
      }
      return {
        ok: true, status: 200,
        json: async () => ({ otpauth_url: 'otpauth://totp/X:Y?secret=ABC&issuer=X', manual_key: 'ABCDEF' }),
      };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => otp }, qrImpl);
    await settle();
    expect(byData.start.hidden).toBe(false);
    byData['start-btn'].click();
    await settle();
    expect(byData.setup.hidden).toBe(false);
    expect(byData.key.textContent).toBe('ABCDEF');
    expect(qrDrawn).toBe(true);
    expect(byData['qr-fallback'].hidden).toBe(true);
  });

  it('wrong confirm code shows the plain message, clears boxes, keeps QR + key', async () => {
    const otp = fakeOtp();
    const fetchImpl = async (url, options) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: false, setup_required: true, pending_request_kind: null }) };
      }
      if (url.endsWith('/setup')) {
        return { ok: true, status: 200, json: async () => ({ otpauth_url: 'otpauth://totp/X:Y?secret=ABC&issuer=X', manual_key: 'KEY123' }) };
      }
      return { ok: false, status: 400, json: async () => ({ detail: 'Invalid authenticator code.' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => otp }, fakeQr());
    await settle();
    byData['start-btn'].click();
    await settle();
    otp.set('123456');
    byData.confirm.click();
    await settle();
    expect(byData.error.textContent).toBe('Invalid authenticator code.');
    expect(byData.error.hidden).toBe(false);
    expect(otp.value).toBe('');
    expect(byData.setup.hidden).toBe(false);
    expect(byData.key.textContent).toBe('KEY123');
  });

  it('confirm success clears the key and shows enrolled', async () => {
    const otp = fakeOtp();
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: false, setup_required: true, pending_request_kind: null }) };
      }
      if (url.endsWith('/setup')) {
        return { ok: true, status: 200, json: async () => ({ otpauth_url: 'otpauth://totp/X:Y?secret=ABC&issuer=X', manual_key: 'KEY999' }) };
      }
      return { ok: true, status: 200, json: async () => ({ message: 'Authenticator MFA is now on.' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => otp }, fakeQr());
    await settle();
    byData['start-btn'].click();
    await settle();
    otp.set('654321');
    byData.confirm.click();
    await settle();
    expect(byData.key.textContent).toBe('');
    expect(byData.enrolled.hidden).toBe(false);
    expect(byData.setup.hidden).toBe(true);
    expect(byData.status.textContent).toContain('Authenticator is now on');
  });

  it('disable with a wrong code is rejected and the boxes are cleared', async () => {
    const otp = fakeOtp();
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: true, setup_required: false, pending_request_kind: null }) };
      }
      return { ok: false, status: 400, json: async () => ({ detail: 'Invalid authenticator code.' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => otp }, fakeQr());
    await settle();
    expect(byData.enrolled.hidden).toBe(false);
    byData['disable-btn'].click();
    await settle();
    expect(byData.disable.hidden).toBe(false);
    expect(byData.enrolled.hidden).toBe(true);
    otp.set('000000');
    byData['disable-confirm'].click();
    await settle();
    expect(byData['disable-error'].textContent).toBe('Invalid authenticator code.');
    expect(otp.value).toBe('');
    expect(byData.disable.hidden).toBe(false);
  });

  it('disable success shows the sign-in notice and the start state', async () => {
    const disableOtp = fakeOtp();
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: true, setup_required: false, pending_request_kind: null }) };
      }
      return { ok: true, status: 200, json: async () => ({ message: 'Authenticator MFA is now off. Sign in with your password.' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => disableOtp }, fakeQr());
    await settle();
    byData['disable-btn'].click();
    await settle();
    disableOtp.set('123456');
    byData['disable-confirm'].click();
    await settle();
    expect(byData.signin.hidden).toBe(false);
    expect(byData.start.hidden).toBe(false);
    expect(byData.enrolled.hidden).toBe(true);
    expect(byData.status.textContent).toContain('Authenticator is now off');
  });

  it('reset request shows the generic message and the pending notice', async () => {
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: true, setup_required: false, pending_request_kind: null }) };
      }
      return { ok: true, status: 200, json: async () => ({ message: 'Request received. Another Super Admin will review it.' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => fakeOtp() }, fakeQr());
    await settle();
    byData['reset-link'].click();
    await settle();
    expect(byData.reset.hidden).toBe(false);
    byData['reset-email'].value = 'admin@example.test';
    byData['reset-btn'].click();
    await settle();
    expect(byData['reset-msg'].textContent).toBe('Request received. Another Super Admin will review it.');
    expect(byData['reset-msg'].hidden).toBe(false);
    expect(byData.pending.hidden).toBe(false);
  });

  it('setup 503 shows the plain unavailable message', async () => {
    const fetchImpl = async (url) => {
      if (url.endsWith('/status')) {
        return { ok: true, status: 200, json: async () => ({ enrolled: false, setup_required: true, pending_request_kind: null }) };
      }
      return { ok: false, status: 503, json: async () => ({ detail: 'anything technical here must not leak' }) };
    };
    const { byData } = makeEnv(fetchImpl, { create: () => fakeOtp() }, fakeQr());
    await settle();
    byData['start-btn'].click();
    await settle();
    expect(byData.error.textContent).toBe('Authenticator setup is not available right now.');
    expect(byData.error.textContent).not.toContain('technical');
  });
});
