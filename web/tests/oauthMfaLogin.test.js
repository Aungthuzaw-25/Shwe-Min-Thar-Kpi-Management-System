import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(path.join(repo, rel), 'utf8');

// OAuth MFA code-entry step (2026-11-07): the backend redirects an
// enrolled OAuth user to /auth/login?oauth_mfa_challenge=<token> with
// NO session, and the login page REUSES its existing OTP step to
// collect the code — no second OTP UI. The contract is read from
// app/modules/auth/router.py (verify_oauth_mfa) + schemas.py
// (OAuthMfaVerifyRequest): POST /api/v1/auth/oauth/mfa-verify
// {challenge, totp_code} -> {redirect_to} on success, 401 MFA_INVALID
// / MFA_REQUIRED, 503 MFA_UNAVAILABLE, 400 "Invalid OAuth state."
// on a dead challenge. The challenge token lives only in the URL
// param -> verify body: never logged, never stored.
describe('OAuth MFA login step (reuses the login OTP step)', () => {
  const login = read('templates/auth/login.njk');

  it('detects the challenge param and shows the OTP step on load', () => {
    expect(login).toContain(
      "new URLSearchParams(window.location.search).get('oauth_mfa_challenge')",
    );
    expect(login).toContain('if (oauthChallenge) setLoginStep(true);');
  });

  it('verifies through /oauth/mfa-verify with the challenge + code, then follows redirect_to', () => {
    expect(login).toContain("fetch('/api/v1/auth/oauth/mfa-verify'");
    expect(login).toContain('{ challenge: oauthChallenge, totp_code: totpCode }');
    expect(login).toContain('verifyData.redirect_to');
    // Cookie session: no localStorage user write on the OAuth path.
    expect(login).not.toContain('localStorage.setItem("user", JSON.stringify(verifyData');
  });

  it('maps verify failures to the same messages as the password OTP step', () => {
    expect(login).toContain("verifyError.detail?.code === 'MFA_INVALID'");
    expect(login).toContain("'Invalid authenticator code.'");
    expect(login).toContain("verifyError.detail?.code === 'MFA_UNAVAILABLE'");
    expect(login).toContain("'Authenticator verification is not available right now.'");
    // Dead challenge (400 string detail) surfaces the server's words.
    expect(login).toContain("typeof verifyDetail === 'string' ? verifyDetail");
  });

  it('never logs or stores the challenge token', () => {
    // Exactly two occurrences in the whole page: the URL detection
    // and the verify body. Any new use (storage, logging, another
    // endpoint) changes this count and must be justified.
    expect(login.match(/oauth_mfa_challenge/g)).toHaveLength(2);
    expect(login).not.toContain('console.log(oauthChallenge');
    expect(login).not.toContain('sessionStorage');
  });

  it('adds no inline handlers to the login page', () => {
    // The page is pinned at 4 onclick= attributes; the OAuth step
    // wires everything through the existing script.
    expect(login.match(/onclick=/g)).toHaveLength(4);
  });

  it('unblocks the submit in OAuth mode: hidden empty password fields must not fail native validation', () => {
    // The email/password inputs are required, but on the code step
    // they are hidden — empty in OAuth mode (never filled), so
    // native validation would block the submit before the handler
    // runs. The shared step function drops required on the code
    // step and restores it on the way back.
    expect(login).toContain("document.getElementById('email').required = !showOtp;");
    expect(login).toContain("document.getElementById('password').required = !showOtp;");
  });

  it('leaves the password login path untouched', () => {
    expect(login).toContain("fetch('/api/v1/auth/login'");
    expect(login).toContain('body = { email, password, remember_me: rememberMe }');
    expect(login).toContain('if (totpCode) body.totp_code = totpCode;');
  });
});
