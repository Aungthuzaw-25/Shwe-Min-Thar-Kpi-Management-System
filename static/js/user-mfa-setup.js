// Admin/HR optional Authenticator setup card on the account profile
// Security section. Admin/HR only: the card markup itself is
// server-gated in account_profile_body.njk (user_role in Admin/HR),
// so this script only ever runs for an Admin or HR user, and it stops
// immediately if its card is absent. Super Admins keep the separate
// SA card (sa-mfa-setup.js); Department Users / Employees get no card
// and no scripts.
//
// Flow (Backend already live on this branch, reused as-is):
//   GET  /api/v1/auth/user-mfa/status   -> enrolled? enrolled : start
//   POST /api/v1/auth/user-mfa/setup    -> { otpauth_url, manual_key }
//   POST /api/v1/auth/user-mfa/confirm  { code } -> MFA on
//   POST /api/v1/auth/user-mfa/disable  { code } -> MFA off (all
//        sessions revoked; the user signs in again password-only)
//   POST /api/v1/auth/user-mfa/reset-requests { email } -> always the
//        same generic reply (an SA reviews it; never self-approved)
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
  var root = document.getElementById('userMfaCard');
  if (!root) return;

  var API_STATUS = '/api/v1/auth/user-mfa/status';
  var API_SETUP = '/api/v1/auth/user-mfa/setup';
  var API_CONFIRM = '/api/v1/auth/user-mfa/confirm';
  var API_DISABLE = '/api/v1/auth/user-mfa/disable';
  var API_RESET = '/api/v1/auth/user-mfa/reset-requests';
  var MSG_UNAVAILABLE = 'Authenticator setup is not available right now.';
  var MSG_GENERIC = 'Something went wrong. Try again.';
  var MSG_INVALID_CODE = 'Invalid authenticator code.';
  var MSG_RESET_RECEIVED = 'Request received. Another Super Admin will review it.';

  var statusEl = root.querySelector('[data-user-mfa-status]');
  var signinEl = root.querySelector('[data-user-mfa-signin]');
  var startEl = root.querySelector('[data-user-mfa-start]');
  var setupEl = root.querySelector('[data-user-mfa-setup]');
  var enrolledEl = root.querySelector('[data-user-mfa-enrolled]');
  var disableEl = root.querySelector('[data-user-mfa-disable]');
  var resetEl = root.querySelector('[data-user-mfa-reset]');
  var startBtn = root.querySelector('[data-user-mfa-start-btn]');
  var qrCanvas = root.querySelector('[data-user-mfa-qr]');
  var qrFallback = root.querySelector('[data-user-mfa-qr-fallback]');
  var keyEl = root.querySelector('[data-user-mfa-key]');
  var copyBtn = root.querySelector('[data-user-mfa-copy]');
  var otpBox = root.querySelector('[data-user-mfa-otp]');
  var confirmBtn = root.querySelector('[data-user-mfa-confirm]');
  var cancelBtn = root.querySelector('[data-user-mfa-cancel]');
  var errorEl = root.querySelector('[data-user-mfa-error]');
  var disableBtn = root.querySelector('[data-user-mfa-disable-btn]');
  var disableOtpBox = root.querySelector('[data-user-mfa-disable-otp]');
  var disableConfirmBtn = root.querySelector('[data-user-mfa-disable-confirm]');
  var disableCancelBtn = root.querySelector('[data-user-mfa-disable-cancel]');
  var disableErrorEl = root.querySelector('[data-user-mfa-disable-error]');
  var resetLinkBtn = root.querySelector('[data-user-mfa-reset-link]');
  var resetEmail = root.querySelector('[data-user-mfa-reset-email]');
  var resetBtn = root.querySelector('[data-user-mfa-reset-btn]');
  var resetCancelBtn = root.querySelector('[data-user-mfa-reset-cancel]');
  var resetErrorEl = root.querySelector('[data-user-mfa-reset-error]');
  var resetMsgEl = root.querySelector('[data-user-mfa-reset-msg]');
  var pendingEl = root.querySelector('[data-user-mfa-pending]');

  // The pending secret lives ONLY here. Never assigned to storage, to
  // the URL, or to any log call.
  var pendingUrl = null;
  var pendingKey = null;
  var otp = null;
  var disableOtp = null;

  function show(el, visible) {
    if (el) el.hidden = !visible;
  }

  function setStatus(text) {
    if (statusEl) statusEl.textContent = text;
  }

  function setError(el, text) {
    if (!el) return;
    el.hidden = !text;
    if (text) el.textContent = text;
  }

  function showStart() {
    show(startEl, true);
    show(setupEl, false);
    show(enrolledEl, false);
    show(disableEl, false);
    show(resetEl, false);
  }

  function showEnrolled() {
    show(startEl, false);
    show(setupEl, false);
    show(enrolledEl, true);
    show(disableEl, false);
    show(resetEl, false);
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
    if (disableOtp) disableOtp.clear();
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
        show(pendingEl, !!result.data.pending_request_kind);
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
    setError(errorEl, '');
    if (startBtn) startBtn.disabled = true;
    try {
      var result = await callApi(API_SETUP, {});
      if (result.ok && result.data && result.data.otpauth_url && result.data.manual_key) {
        pendingUrl = result.data.otpauth_url;
        pendingKey = result.data.manual_key;
        if (!window.OtpInput) {
          setError(errorEl, 'Code input is not available. Reload the page and try again.');
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
        show(disableEl, false);
        show(resetEl, false);
        show(setupEl, true);
        otp.focus();
      } else if (result.status === 400) {
        // Setup's only 400 is "already enrolled".
        setStatus('Authenticator is on.');
        showEnrolled();
      } else if (result.status === 503) {
        setError(errorEl, MSG_UNAVAILABLE);
      } else {
        setError(errorEl, MSG_GENERIC);
      }
    } catch (e) {
      setError(errorEl, MSG_GENERIC);
    }
    if (startBtn) startBtn.disabled = false;
  }

  async function confirmCode() {
    setError(errorEl, '');
    if (!otp) return;
    var code = otp.getValue();
    if (!code || code.length !== 6) {
      setError(errorEl, 'Enter the 6-digit code.');
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
        setError(errorEl, MSG_UNAVAILABLE);
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
          setError(errorEl, MSG_INVALID_CODE);
          otp.clear();
          otp.focus();
        }
      } else {
        setError(errorEl, MSG_GENERIC);
      }
    } catch (e) {
      setError(errorEl, MSG_GENERIC);
    }
    if (confirmBtn) confirmBtn.disabled = false;
  }

  function cancelSetup() {
    clearPending();
    setError(errorEl, '');
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
      setError(errorEl, 'Copy failed — copy the key manually.');
    }
  }

  function showDisable() {
    setError(disableErrorEl, '');
    if (!window.OtpInput) {
      setError(disableErrorEl, 'Code input is not available. Reload the page and try again.');
      return;
    }
    if (!disableOtp) disableOtp = window.OtpInput.create(disableOtpBox);
    disableOtp.clear();
    show(startEl, false);
    show(setupEl, false);
    show(enrolledEl, false);
    show(resetEl, false);
    show(disableEl, true);
    disableOtp.focus();
  }

  async function confirmDisable() {
    setError(disableErrorEl, '');
    if (!disableOtp) return;
    var code = disableOtp.getValue();
    if (!code || code.length !== 6) {
      setError(disableErrorEl, 'Enter the 6-digit code.');
      disableOtp.focus();
      return;
    }
    if (disableConfirmBtn) disableConfirmBtn.disabled = true;
    try {
      var result = await callApi(API_DISABLE, { code: code });
      if (result.ok) {
        // All sessions are revoked server-side, including this one.
        clearPending();
        setStatus('Authenticator is now off.');
        show(signinEl, true);
        showStart();
      } else if (result.status === 503) {
        setError(disableErrorEl, MSG_UNAVAILABLE);
      } else if (result.status === 400) {
        var detail = result.data && result.data.detail;
        if (detail === 'MFA is not enrolled.') {
          clearPending();
          setStatus('Authenticator is not set up yet.');
          showStart();
        } else {
          // Wrong code: clear the boxes, stay on the step.
          setError(disableErrorEl, MSG_INVALID_CODE);
          disableOtp.clear();
          disableOtp.focus();
        }
      } else {
        setError(disableErrorEl, MSG_GENERIC);
      }
    } catch (e) {
      setError(disableErrorEl, MSG_GENERIC);
    }
    if (disableConfirmBtn) disableConfirmBtn.disabled = false;
  }

  function cancelDisable() {
    if (disableOtp) disableOtp.clear();
    setError(disableErrorEl, '');
    setStatus('Authenticator is on.');
    showEnrolled();
  }

  function showReset() {
    setError(resetErrorEl, '');
    if (resetMsgEl) resetMsgEl.hidden = true;
    show(startEl, false);
    show(setupEl, false);
    show(enrolledEl, false);
    show(disableEl, false);
    show(resetEl, true);
  }

  async function requestReset() {
    setError(resetErrorEl, '');
    if (resetMsgEl) resetMsgEl.hidden = true;
    var email = resetEmail ? (resetEmail.value || '').trim() : '';
    if (!email) {
      setError(resetErrorEl, 'Enter your email.');
      return;
    }
    if (resetBtn) resetBtn.disabled = true;
    try {
      var result = await callApi(API_RESET, { email: email });
      if (result.ok) {
        // The endpoint always answers the same generic message.
        if (resetMsgEl) {
          resetMsgEl.textContent = MSG_RESET_RECEIVED;
          resetMsgEl.hidden = false;
        }
        show(pendingEl, true);
      } else {
        setError(resetErrorEl, MSG_GENERIC);
      }
    } catch (e) {
      setError(resetErrorEl, MSG_GENERIC);
    }
    if (resetBtn) resetBtn.disabled = false;
  }

  function cancelReset() {
    setError(resetErrorEl, '');
    if (resetMsgEl) resetMsgEl.hidden = true;
    setStatus('Authenticator is on.');
    showEnrolled();
  }

  if (startBtn) startBtn.addEventListener('click', startSetup);
  if (confirmBtn) confirmBtn.addEventListener('click', confirmCode);
  if (cancelBtn) cancelBtn.addEventListener('click', cancelSetup);
  if (copyBtn) copyBtn.addEventListener('click', copyKey);
  if (disableBtn) disableBtn.addEventListener('click', showDisable);
  if (disableConfirmBtn) disableConfirmBtn.addEventListener('click', confirmDisable);
  if (disableCancelBtn) disableCancelBtn.addEventListener('click', cancelDisable);
  if (resetLinkBtn) resetLinkBtn.addEventListener('click', showReset);
  if (resetBtn) resetBtn.addEventListener('click', requestReset);
  if (resetCancelBtn) resetCancelBtn.addEventListener('click', cancelReset);
  window.addEventListener('pagehide', clearPending);

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', loadStatus);
  } else {
    loadStatus();
  }
})();
