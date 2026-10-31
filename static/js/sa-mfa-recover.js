// Super Admin account recovery entry on the login page ("Recover with
// Authenticator"). For an SA who forgot their password but still has
// their authenticator app: email + current 6-digit code proves identity
// (POST /api/v1/auth/sa-mfa/recover), then a one-time new-password form
// (POST /api/v1/auth/sa-mfa/reset-password, no old password, flag clears
// on use, other sessions revoked server-side).
//
// This script is deliberately separate from the login submit handler:
// the first password-only login and the enrolled password+code login
// are untouched, and no "trust this device" checkbox or bypass exists
// anywhere here — every enrolled password login still asks for the code.
//
// SECURITY — the recover step reveals nothing about the account:
//   - EVERY recover failure (unknown email, non-SA, unenrolled, frozen,
//     wrong/replayed code, rate limit, network error) shows only the
//     backend's generic failure, and the failure body is never read at
//     all, so no distinguishing detail can leak. The one exception is
//     the backend's own config failure (503, Fernet not configured),
//     which shows its fixed plain verification message — it reveals
//     nothing about the account either;
//   - no client retry loop (the backend rate limit is 5/minute);
//   - email and code are never logged or stored; the code boxes and
//     password fields are cleared whenever the modal closes;
//   - no inline handlers: everything below is addEventListener, so no
//     CSP-bridge allowlist entry is needed.
(function () {
  var modal = document.getElementById('saRecoverModal');
  if (!modal) return;

  var API_RECOVER = '/api/v1/auth/sa-mfa/recover';
  var API_RESET = '/api/v1/auth/sa-mfa/reset-password';
  var MSG_GENERIC = 'Invalid email or authenticator code.';
  var MSG_UNAVAILABLE = 'Authenticator verification is not available right now.';

  var entryBtn = document.getElementById('saRecoverEntryBtn');
  var closeXBtn = document.getElementById('saRecoverCloseXBtn');
  var stepCode = modal.querySelector('[data-recover-step="code"]');
  var stepReset = modal.querySelector('[data-recover-step="reset"]');
  var stepDone = modal.querySelector('[data-recover-step="done"]');
  var emailEl = document.getElementById('saRecoverEmail');
  var otpBoxesEl = document.getElementById('saRecoverOtpBoxes');
  var errorEl = document.getElementById('saRecoverError');
  var recoverBtn = document.getElementById('saRecoverSubmitBtn');
  var cancelBtn = document.getElementById('saRecoverCancelBtn');
  var newPwEl = document.getElementById('saRecoverNewPassword');
  var confirmPwEl = document.getElementById('saRecoverConfirmPassword');
  var resetErrorEl = document.getElementById('saRecoverResetError');
  var resetBtn = document.getElementById('saRecoverResetBtn');
  var resetCancelBtn = document.getElementById('saRecoverResetCancelBtn');
  var doneBtn = document.getElementById('saRecoverDoneBtn');

  var otp = null;

  function showStep(name) {
    if (stepCode) stepCode.classList.toggle('hidden', name !== 'code');
    if (stepReset) stepReset.classList.toggle('hidden', name !== 'reset');
    if (stepDone) stepDone.classList.toggle('hidden', name !== 'done');
  }

  function setError(el, text) {
    if (!el) return;
    el.classList.toggle('hidden', !text);
    if (text) el.textContent = text;
  }

  function clearSensitive() {
    if (otp) otp.clear();
    if (newPwEl) newPwEl.value = '';
    if (confirmPwEl) confirmPwEl.value = '';
    setError(errorEl, '');
    setError(resetErrorEl, '');
  }

  function openRecover() {
    showStep('code');
    setError(errorEl, '');
    setError(resetErrorEl, '');
    if (otpBoxesEl && window.OtpInput && !otp) otp = window.OtpInput.create(otpBoxesEl);
    modal.classList.remove('hidden');
    if (window.SharedDialog) window.SharedDialog.open(modal, closeRecover);
    if (otp) otp.focus();
    else if (emailEl) emailEl.focus();
  }

  function closeRecover() {
    if (window.SharedDialog) window.SharedDialog.close(modal);
    modal.classList.add('hidden');
    clearSensitive();
    showStep('code');
  }

  async function submitRecover() {
    setError(errorEl, '');
    var email = emailEl ? emailEl.value.trim() : '';
    if (!email) {
      setError(errorEl, 'Enter your email address.');
      if (emailEl) emailEl.focus();
      return;
    }
    if (!otp) {
      setError(errorEl, 'Code input is not available. Reload the page and try again.');
      return;
    }
    var code = otp.getValue();
    if (!code || code.length !== 6) {
      setError(errorEl, 'Enter the 6-digit code.');
      otp.focus();
      return;
    }
    if (recoverBtn) recoverBtn.disabled = true;
    try {
      var response = await fetch(API_RECOVER, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ email: email, code: code }),
      });
      if (response.ok) {
        // The code is spent: clear it before showing the reset form.
        otp.clear();
        showStep('reset');
        if (newPwEl) newPwEl.focus();
      } else if (response.status === 503) {
        // Config failure only (Fernet not configured): the backend's
        // fixed plain message, which reveals nothing about the account.
        setError(errorEl, MSG_UNAVAILABLE);
        otp.clear();
        otp.focus();
      } else {
        // Deliberately generic for every other failure; the body is
        // not read, so no distinguishing detail can leak.
        setError(errorEl, MSG_GENERIC);
        otp.clear();
        otp.focus();
      }
    } catch (e) {
      setError(errorEl, MSG_GENERIC);
      otp.clear();
      otp.focus();
    }
    if (recoverBtn) recoverBtn.disabled = false;
  }

  async function submitReset() {
    setError(resetErrorEl, '');
    var next = newPwEl ? newPwEl.value : '';
    var confirm = confirmPwEl ? confirmPwEl.value : '';
    if (!next || next.length < 10) {
      setError(resetErrorEl, 'New password must be at least 10 characters.');
      if (newPwEl) newPwEl.focus();
      return;
    }
    if (next !== confirm) {
      setError(resetErrorEl, 'New password confirmation does not match.');
      if (confirmPwEl) confirmPwEl.focus();
      return;
    }
    if (resetBtn) resetBtn.disabled = true;
    try {
      var response = await fetch(API_RESET, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ new_password: next, confirm_new_password: confirm }),
      });
      if (response.ok) {
        if (newPwEl) newPwEl.value = '';
        if (confirmPwEl) confirmPwEl.value = '';
        showStep('done');
      } else if (typeof window.readServerError === 'function') {
        setError(resetErrorEl, await window.readServerError(response));
      } else {
        setError(resetErrorEl, 'Something went wrong. Try again.');
      }
    } catch (e) {
      setError(resetErrorEl, 'Something went wrong. Try again.');
    }
    if (resetBtn) resetBtn.disabled = false;
  }

  if (entryBtn) entryBtn.addEventListener('click', openRecover);
  if (closeXBtn) closeXBtn.addEventListener('click', closeRecover);
  if (cancelBtn) cancelBtn.addEventListener('click', closeRecover);
  if (resetCancelBtn) resetCancelBtn.addEventListener('click', closeRecover);
  if (doneBtn) doneBtn.addEventListener('click', closeRecover);
  if (recoverBtn) recoverBtn.addEventListener('click', submitRecover);
  if (resetBtn) resetBtn.addEventListener('click', submitReset);
})();
