// Super Admin break-glass password reset on the login page. For an SA
// who never set up Authenticator and forgot the password: email +
// break-glass token + new password (POST
// /api/v1/auth/sa-mfa/break-glass-reset-password), no current password,
// no TOTP. Kept separate from the recover modal ("Recover with
// Authenticator"), which is the path for enrolled SAs.
//
// SECURITY — the token is the owner-held emergency credential:
//   - password-type input, never logged, never stored in any browser
//     storage, never put in a page URL — held only in the field;
//   - cleared (with the passwords) whenever the modal closes, on
//     success, on a 401, and on pagehide. The email is kept — it is
//     not a secret, like the recover form;
//   - every 401 / network / other failure shows one fixed generic
//     message that never says which part was wrong. The 400s are
//     input validation (strength, mismatch, reuse) and show the
//     server's reason via readServerError, like the recover reset
//     form; the 503 shows the backend's fixed unavailable message;
//   - no client retry loop (the backend rate limit is 5/minute);
//   - no inline handlers: everything below is addEventListener, so no
//     CSP-bridge allowlist entry is needed.
(function () {
  var modal = document.getElementById('saBreakGlassModal');
  if (!modal) return;

  var API_RESET = '/api/v1/auth/sa-mfa/break-glass-reset-password';
  var MSG_GENERIC = 'Invalid email, break-glass token, or account state.';
  var MSG_UNAVAILABLE = 'Authenticator break-glass recovery is not available right now.';
  var MSG_STRENGTH = 'Use at least 12 characters, with an uppercase letter, a lowercase letter, a number, and a special character.';
  var MSG_MISMATCH = 'New password confirmation does not match.';

  var entryBtn = document.getElementById('saBreakGlassEntryBtn');
  var closeXBtn = document.getElementById('saBreakGlassCloseXBtn');
  var stepForm = modal.querySelector('[data-breakglass-step="form"]');
  var stepDone = modal.querySelector('[data-breakglass-step="done"]');
  var emailEl = document.getElementById('saBreakGlassEmail');
  var tokenEl = document.getElementById('saBreakGlassToken');
  var newPwEl = document.getElementById('saBreakGlassNewPassword');
  var confirmPwEl = document.getElementById('saBreakGlassConfirmPassword');
  var errorEl = document.getElementById('saBreakGlassError');
  var doneMessageEl = document.getElementById('saBreakGlassDoneMessage');
  var submitBtn = document.getElementById('saBreakGlassSubmitBtn');
  var cancelBtn = document.getElementById('saBreakGlassCancelBtn');
  var doneBtn = document.getElementById('saBreakGlassDoneBtn');

  // Server password policy, default values (password_policy.py):
  // minimum 12 characters + one uppercase, one lowercase, one digit,
  // one special character. The server stays the authority — its 400
  // is shown when the configured policy differs — this pre-check
  // only saves a round trip for the common case.
  function strongEnough(password) {
    if (!password || password.length < 12) return false;
    if (!/[A-Z]/.test(password)) return false;
    if (!/[a-z]/.test(password)) return false;
    if (!/\d/.test(password)) return false;
    if (!/[!@#$%^&*(),.?\"{}|<>]/.test(password)) return false;
    return true;
  }

  function showStep(name) {
    if (stepForm) stepForm.classList.toggle('hidden', name !== 'form');
    if (stepDone) stepDone.classList.toggle('hidden', name !== 'done');
  }

  function setError(text) {
    if (!errorEl) return;
    errorEl.classList.toggle('hidden', !text);
    if (text) errorEl.textContent = text;
  }

  function clearSensitive() {
    if (tokenEl) tokenEl.value = '';
    if (newPwEl) newPwEl.value = '';
    if (confirmPwEl) confirmPwEl.value = '';
    setError('');
  }

  function openBreakGlass() {
    showStep('form');
    setError('');
    modal.classList.remove('hidden');
    if (window.SharedDialog) window.SharedDialog.open(modal, closeBreakGlass);
    if (emailEl) emailEl.focus();
  }

  function closeBreakGlass() {
    if (window.SharedDialog) window.SharedDialog.close(modal);
    modal.classList.add('hidden');
    clearSensitive();
    showStep('form');
  }

  async function submitReset() {
    setError('');
    var email = emailEl ? emailEl.value.trim() : '';
    if (!email) {
      setError('Enter your email address.');
      if (emailEl) emailEl.focus();
      return;
    }
    var token = tokenEl ? tokenEl.value : '';
    if (!token) {
      setError('Enter the break-glass token.');
      if (tokenEl) tokenEl.focus();
      return;
    }
    var next = newPwEl ? newPwEl.value : '';
    if (!strongEnough(next)) {
      setError(MSG_STRENGTH);
      if (newPwEl) newPwEl.focus();
      return;
    }
    var confirm = confirmPwEl ? confirmPwEl.value : '';
    if (next !== confirm) {
      setError(MSG_MISMATCH);
      if (confirmPwEl) confirmPwEl.focus();
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    try {
      var response = await fetch(API_RESET, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({
          email: email,
          break_glass_token: token,
          new_password: next,
          confirm_new_password: confirm,
        }),
      });
      if (response.ok) {
        // The token is spent for this reset: clear it (and the
        // passwords) before showing the done step.
        var message = '';
        try {
          var data = await response.json();
          message = (data && data.message) || '';
        } catch (e) { message = ''; }
        clearSensitive();
        if (doneMessageEl) doneMessageEl.textContent = message || 'Password reset. Sign in with your new password.';
        showStep('done');
      } else if (response.status === 503) {
        // Config failure only (break-glass not configured): the
        // backend's fixed plain message, which reveals nothing
        // about the account. The token stays — this is transient.
        setError(MSG_UNAVAILABLE);
      } else if (response.status === 400) {
        // Input validation (strength, mismatch, reuse): the server's
        // reason, like the recover reset form. The token stays.
        if (typeof window.readServerError === 'function') {
          setError(await window.readServerError(response));
        } else {
          setError('Something went wrong. Try again.');
        }
      } else {
        // Deliberately generic for every other failure (401 included):
        // it never says whether the email, the token, or the account
        // state was wrong. The token may be wrong, so clear it.
        setError(MSG_GENERIC);
        if (tokenEl) tokenEl.value = '';
        if (newPwEl) newPwEl.value = '';
        if (confirmPwEl) confirmPwEl.value = '';
      }
    } catch (e) {
      setError(MSG_GENERIC);
      if (tokenEl) tokenEl.value = '';
      if (newPwEl) newPwEl.value = '';
      if (confirmPwEl) confirmPwEl.value = '';
    }
    if (submitBtn) submitBtn.disabled = false;
  }

  if (entryBtn) entryBtn.addEventListener('click', openBreakGlass);
  if (closeXBtn) closeXBtn.addEventListener('click', closeBreakGlass);
  if (cancelBtn) cancelBtn.addEventListener('click', closeBreakGlass);
  if (doneBtn) doneBtn.addEventListener('click', closeBreakGlass);
  if (submitBtn) submitBtn.addEventListener('click', submitReset);
  window.addEventListener('pagehide', clearSensitive);
})();
