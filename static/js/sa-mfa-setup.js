// Super Admin Authenticator setup card on the account profile Security
// section. SA-only: the card markup itself is server-gated in
// account_profile_body.njk, so this script only ever runs for a Super
// Admin, and it stops immediately if its card is absent.
//
// Flow (Backend already live on main, reused as-is):
//   GET  /api/v1/auth/sa-mfa/status  -> enrolled? enrolled state : start
//   POST /api/v1/auth/sa-mfa/setup   -> { otpauth_url, manual_key }
//   POST /api/v1/auth/sa-mfa/confirm { code } -> MFA on
//
// SECURITY — the otpauth URL and manual key ARE the user's TOTP secret:
//   - rendered only here, in this browser: the QR is drawn locally with
//     the vendored qrcode-generator (static/vendor/), never sent to any
//     external service, image API, or third-party host;
//   - never logged, never stored in any browser storage, never
//     put in a page URL — held only in this closure's variables;
//   - cleared from the DOM and the closure on confirm, cancel, and
//     pagehide (navigation away). Server error text is never rendered:
//     every message below is a fixed plain string chosen by status code.
(function () {
  var root = document.getElementById('saMfaCard');
  if (!root) return;

  var API_STATUS = '/api/v1/auth/sa-mfa/status';
  var API_SETUP = '/api/v1/auth/sa-mfa/setup';
  var API_CONFIRM = '/api/v1/auth/sa-mfa/confirm';
  var MSG_UNAVAILABLE = 'Authenticator setup is not available right now.';
  var MSG_GENERIC = 'Something went wrong. Try again.';
  var MSG_INVALID_CODE = 'Invalid authenticator code.';

  var statusEl = root.querySelector('[data-mfa-status]');
  var startEl = root.querySelector('[data-mfa-start]');
  var setupEl = root.querySelector('[data-mfa-setup]');
  var enrolledEl = root.querySelector('[data-mfa-enrolled]');
  var startBtn = root.querySelector('[data-mfa-start-btn]');
  var qrCanvas = root.querySelector('[data-mfa-qr]');
  var qrFallback = root.querySelector('[data-mfa-qr-fallback]');
  var keyEl = root.querySelector('[data-mfa-key]');
  var copyBtn = root.querySelector('[data-mfa-copy]');
  var otpBox = root.querySelector('[data-mfa-otp]');
  var confirmBtn = root.querySelector('[data-mfa-confirm]');
  var cancelBtn = root.querySelector('[data-mfa-cancel]');
  var errorEl = root.querySelector('[data-mfa-error]');

  // The pending secret lives ONLY here. Never assigned to storage, to
  // the URL, or to any log call.
  var pendingUrl = null;
  var pendingKey = null;
  var otp = null;

  function show(el, visible) {
    if (el) el.hidden = !visible;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function setError(text) {
    if (!errorEl) return;
    errorEl.hidden = !text;
    if (text) errorEl.textContent = text;
  }

  function showStart() {
    show(startEl, true);
    show(setupEl, false);
    show(enrolledEl, false);
  }

  function showEnrolled() {
    show(startEl, false);
    show(setupEl, false);
    show(enrolledEl, true);
  }

  function clearPending() {
    pendingUrl = null;
    pendingKey = null;
    if (qrCanvas) {
      var ctx = qrCanvas.getContext && qrCanvas.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, qrCanvas.width, qrCanvas.height);
    }
    if (keyEl) keyEl.textContent = '';
    if (otp) otp.clear();
  }

  async function callApi(path, body) {
    var options = { credentials: 'same-origin' };
    if (body !== undefined) {
      options.method = 'POST';
      options.headers = { 'Content-Type': 'application/json' };
      options.body = JSON.stringify(body);
    }
    var response = await fetch(path, options);
    var data = {};
    try {
      data = await response.json();
    } catch (e) {
      data = {};
    }
    return { ok: response.ok, status: response.status, data: data };
  }

  async function loadStatus() {
    setStatus('Checking authenticator status…');
    try {
      var result = await callApi(API_STATUS);
      if (result.ok && result.data && result.data.enrolled) {
        setStatus('Authenticator is on.');
        showEnrolled();
      } else if (result.ok) {
        setStatus('Authenticator is not set up yet.');
        showStart();
      } else {
        setStatus('Could not check authenticator status. Try again.');
        showStart();
      }
    } catch (e) {
      setStatus('Could not check authenticator status. Try again.');
      showStart();
    }
  }

  function drawQr(url) {
    // Local render only. If the vendored renderer is unavailable, the
    // manual key below is the whole flow — no external QR service, ever.
    if (typeof window.qrcode !== 'function' || !qrCanvas) {
      show(qrCanvas, false);
      show(qrFallback, true);
      return;
    }
    var qr = window.qrcode(0, 'M');
    qr.addData(url);
    qr.make();
    var count = qr.getModuleCount();
    var cell = Math.max(2, Math.floor(240 / count));
    qrCanvas.width = count * cell;
    qrCanvas.height = count * cell;
    var ctx = qrCanvas.getContext('2d');
    ctx.fillStyle = '#ffffff';
    ctx.fillRect(0, 0, qrCanvas.width, qrCanvas.height);
    qr.renderTo2dContext(ctx, cell);
    show(qrCanvas, true);
    show(qrFallback, false);
  }

  async function startSetup() {
    setError('');
    if (startBtn) startBtn.disabled = true;
    try {
      var result = await callApi(API_SETUP, {});
      if (result.ok && result.data && result.data.otpauth_url && result.data.manual_key) {
        pendingUrl = result.data.otpauth_url;
        pendingKey = result.data.manual_key;
        if (!window.OtpInput) {
          setError('Code input is not available. Reload the page and try again.');
          if (startBtn) startBtn.disabled = false;
          return;
        }
        if (!otp) otp = window.OtpInput.create(otpBox);
        otp.clear();
        drawQr(pendingUrl);
        if (keyEl) keyEl.textContent = pendingKey;
        setStatus('Scan the QR code with Google Authenticator, then confirm with the current code.');
        show(startEl, false);
        show(enrolledEl, false);
        show(setupEl, true);
        otp.focus();
      } else if (result.status === 400) {
        // Setup's only 400 is "already enrolled".
        setStatus('Authenticator is on.');
        showEnrolled();
      } else if (result.status === 503) {
        setError(MSG_UNAVAILABLE);
      } else {
        setError(MSG_GENERIC);
      }
    } catch (e) {
      setError(MSG_GENERIC);
    }
    if (startBtn) startBtn.disabled = false;
  }

  async function confirmCode() {
    setError('');
    if (!otp) return;
    var code = otp.getValue();
    if (!code || code.length !== 6) {
      setError('Enter the 6-digit code.');
      otp.focus();
      return;
    }
    if (confirmBtn) confirmBtn.disabled = true;
    try {
      var result = await callApi(API_CONFIRM, { code: code });
      if (result.ok) {
        clearPending();
        setStatus('Authenticator is now on.');
        showEnrolled();
      } else if (result.status === 503) {
        setError(MSG_UNAVAILABLE);
      } else if (result.status === 400) {
        var detail = result.data && result.data.detail;
        if (detail === 'MFA is already enrolled.') {
          clearPending();
          setStatus('Authenticator is on.');
          showEnrolled();
        } else if (detail === 'Run setup first.') {
          clearPending();
          setStatus('Authenticator is not set up yet.');
          showStart();
        } else {
          // Wrong code: clear the boxes, stay on the step, QR + key
          // stay so the user retries without re-setup.
          setError(MSG_INVALID_CODE);
          otp.clear();
          otp.focus();
        }
      } else {
        setError(MSG_GENERIC);
      }
    } catch (e) {
      setError(MSG_GENERIC);
    }
    if (confirmBtn) confirmBtn.disabled = false;
  }

  function cancelSetup() {
    clearPending();
    setError('');
    setStatus('Authenticator is not set up yet.');
    showStart();
  }

  async function copyKey() {
    if (!pendingKey) return;
    try {
      if (!navigator.clipboard || !navigator.clipboard.writeText) throw new Error('no clipboard');
      await navigator.clipboard.writeText(pendingKey);
      var original = copyBtn.textContent;
      copyBtn.textContent = 'Copied';
      window.setTimeout(function () { copyBtn.textContent = original; }, 2000);
    } catch (e) {
      setError('Copy failed — copy the key manually.');
    }
  }

  if (startBtn) startBtn.addEventListener('click', startSetup);
  if (confirmBtn) confirmBtn.addEventListener('click', confirmCode);
  if (cancelBtn) cancelBtn.addEventListener('click', cancelSetup);
  if (copyBtn) copyBtn.addEventListener('click', copyKey);
  window.addEventListener('pagehide', clearPending);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadStatus);
  } else {
    loadStatus();
  }
})();
