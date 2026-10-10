# Phase 5H-S10 — Controlled Browser Network Evidence & Login 401 Correlation

Investigation time: 2026-10-10 00:43:50 +06:30 (Asia/Yangon)

## Final verdict

**OPERATOR AUTHORIZATION NOT PROVIDED — READ-ONLY REVIEW ONLY**

This phase request explicitly states that it does not authorize a login attempt. No separate operator authorization for one attempt was provided. Accordingly, no browser access, DevTools inspection, login, or new runtime evidence collection was performed.

**Historical 401 assessment:** the existing S6 QA API log entry is temporally associated with that phase's controlled attempt window, but the native browser Network event and response were unavailable. The exact browser-to-log request correlation and the 401 cause remain unproven.

Authentication remains **BLOCKED — AUTHENTICATION UNVERIFIED**.

## Initial repository state and QA safety gate

- Repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- HEAD: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Initial `git status --short --branch --untracked-files=all` showed pre-existing tracked modifications to:
  - `static/js/dashboard.js`
  - `static/js/executive-dashboard.js`
  - `templates/auth/change_password.html`
  - `templates/dashboard/notifications.html`
  - `templates/layouts/base.html`
  - `templates/layouts/employee_base.html`
  - `tests/test_csrf_middleware.py`
- It also showed pre-existing untracked QA evidence reports and `tests/test_password_change_csrf.py`; these were preserved.
- Proposed report path `qa-evidence/phase5h-network-401-correlation-20261010.md` was absent before creation.
- Docker context: `desktop-linux`.
- `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa` were running and labeled with Compose project `kpi_management_system_qa`.
- Allowlisted API identity: `ENV=qa`, host `postgres-qa`, port `5432`, database `kpi_qa`.
- No production resources were inspected or contacted.

## Authorization and browser evidence

- This phase required a separate explicit authorization before interacting with or submitting the shared browser page.
- No such authorization was provided in this phase; only read-only source and existing report review proceeded.
- The current shared browser page was not read or interacted with. Its URL, DOM, form state, DevTools state, and Network history were not inspected in this phase.
- Prior reports (S4/S5) state that the browser integration exposes page snapshots but not native DevTools Network events or historical request/response metadata. That prior limitation was not retested because browser inspection was outside the authorized read-only source/evidence path.
- No new login, `/api/v1/auth/me`, or other HTTP/API request was made.

## Existing evidence reviewed

Read-only review covered:

- `qa-evidence/phase5h-login-handler-401-investigation-20261010.md`
- `qa-evidence/phase5h-login-disabled-submit-diagnosis-20261010.md`
- `qa-evidence/phase5h-controlled-auth-runtime-evidence-20261010.md`
- `qa-evidence/phase5h-controlled-auth-runtime-retry-evidence-20261010.md`
- `qa-evidence/phase5h-controlled-auth-operator-preparation-20261010.md`
- `qa-evidence/phase5h-network-capture-capability-20261010.md`
- `qa-evidence/phase5h-network-history-inspection-20261009.md`
- `qa-evidence/phase5h-auth-log-correlation-20261009.md`
- `qa-evidence/phase5h-auth-root-cause-diagnosis-20261009.md`

The S6 runtime report records a QA access-log line for `POST /api/v1/auth/login`, HTTP 401, request ID `3ae657f8-d339-4875-b55a-7618bce1d892`, process time `15892.95ms`, timestamp `2026-10-09T17:52:38.474Z`. It falls within S6's recorded attempt window. However:

- The browser Network event, browser-observed response status/headers, and response body were not available.
- The request ID is from the server access log; a matching browser `X-Request-ID` was not independently captured.
- The bounded S6 log review found no `/api/v1/auth/me` access-log entry.
- S7's separate retry window had no matching auth access-log entry and must not be conflated with the S6 HTTP 401.
- S8/S9's later dashboard URL is not evidence of the S6/S7 authentication outcome.

Thus the existing log supports that a request to the QA login path was processed and logged as HTTP 401 in the S6 window. Exact browser-event correlation is incomplete, and the log contains no rejection reason or response body.

## Source paths and relevant line references

Line numbers below are from the current source reviewed in this phase.

### Login page and client handler

- `templates/auth/login.html:187-233`: login form, required email/password controls, and submit button; the button has no initial `disabled` attribute.
- `templates/auth/login.html:288-290`: loads `csp-event-bridge.js`, `csrf.js`, and `notifications.js`.
- `templates/auth/login.html:382-384`: remembered-email initialization; does not disable the login submit button.
- `templates/auth/login.html:418-436`: one login-form submit listener; sets the loading state and `disabled = true`, then awaits same-origin `POST /api/v1/auth/login` with `credentials: 'same-origin'`.
- `templates/auth/login.html:438-464`: success handling and dashboard/password-change navigation.
- `templates/auth/login.html:465-471`: non-OK response JSON is passed to `window.showToast`; rejected fetch/processing errors use a generic network toast.
- `templates/auth/login.html:472-475`: `finally` restores the button label and enables it after operations inside the `try` settle, unless navigation/execution-context teardown prevents continuation.
- `static/js/notifications.js:20-38`: toast rendering uses `text.textContent` for the supplied message and masks matching technical error text; it does not interpolate the login error into HTML.

### Web/API routes, authentication policy, and CSRF

- `app/web/auth_web.py:9-15`: GET `/auth/login` renders `auth/login.html`.
- `app/modules/auth/router.py:118-119`: GET `/api/v1/auth/me` depends on `get_current_user`.
- `app/modules/auth/router.py:168-189`: POST `/api/v1/auth/login` calls `AuthService.authenticate_user` and sets auth cookies only after successful authentication.
- `app/modules/auth/service.py:22-22,32-85`: login service rejection branches:
  - Unknown account: HTTP 401, generic invalid-email-or-password detail (`51-53`).
  - Inactive or deleted account: HTTP 403 (`55-56`).
  - Currently locked account: HTTP 403 (`58-61`); an expired lockout is handled separately in this code.
  - Local password disabled or absent: HTTP 401 with the same generic detail (`68-70`).
  - Password verification failure: records a failed attempt; HTTP 401 unless the threshold locks the account, which returns HTTP 403 (`73-85`).
- `app/main.py:158-172`: non-5xx `HTTPException` details are returned in JSON; 5xx details are replaced with a generic message.
- `app/main.py:265-296`: CSRF middleware exempts `/api/v1/auth/login` (`277-281`). CSRF mismatch for protected cookie-authenticated mutations returns HTTP 403 (`282-288`), so that middleware's rejection branch does not explain the historical HTTP 401.
- `app/core/dependencies.py:180-265`: `/me` authentication can return 401 for missing token, invalid/expired token, revoked token/session, or missing/inactive user; locked user returns 403. No `/me` response was captured for S6.

### Request logging and focused tests

- `app/middleware/logging_middleware.py:17-23,43-54`: creates a request ID, returns `X-Request-ID` and `X-Process-Time`, and logs request ID, method, path, status, and processing time. It does not log credentials, request bodies, auth cookies, or the login service's rejection branch.
- `tests/test_auth_service.py:68-76`: unit test verifies unknown-email login rejection as HTTP 401.
- `tests/test_api_endpoints.py:51-87`: login route success test uses synthetic fixtures and mocks.
- `tests/test_api_endpoints.py:90-118`: `/me` route test uses dependency overrides.
- `tests/test_csrf_middleware.py:32-101`: focused CSRF validation tests for mutations; no live login or credentials.
- Tests were not run during this phase.

## What the evidence proves

- Source has a single login submit listener that disables the button before awaiting the login request and re-enables it in `finally`.
- A normal settled non-OK response, including HTTP 401, is inside that `try/finally`; the source path ordinarily reaches cleanup. The `finally` does not prove that a particular past browser context received the response or executed the cleanup.
- The S6 QA API access log records a login-path POST with HTTP 401, a request ID, and process duration during the S6 attempt window.
- The login endpoint is exempt from this CSRF middleware; this middleware's CSRF rejection is HTTP 403.
- The client displays non-OK backend error text through a `textContent`-based toast, subject to the toast helper's technical-error masking.

## What remains unproven

- The exact S6 browser Network request and response, including browser-observed response headers or body.
- Exact browser-to-server request-ID correlation for S6.
- Which service branch produced the HTTP 401. The status alone cannot distinguish unknown account, disabled local-password path, or incorrect password; the response body was not retained.
- Whether an authenticated cookie/session was established, whether `/api/v1/auth/me` was naturally issued, or any authenticated identity/role.
- Whether the button returned to enabled after S6's 401, and the cause of the S7 disabled-button state.
- Whether current native DevTools Network capture is usable; not inspected in this phase.

## Integrity and restrictions

- No browser interaction, authentication attempt, HTTP/API request, or new QA log query occurred.
- No credentials, password hashes, tokens, cookies, authorization headers, browser storage, account/session state, or database records were accessed or changed.
- No source, test, template, configuration, middleware, or dependency was changed.
- No production access, deployment, performance test, or recovery/reset/unlock operation occurred.
- `app/phase5c_diagnostics.py` remains absent; no Phase 5C instrumentation was introduced.
- The proposed report path was confirmed absent before creation. This is the only file added by this phase.
- `git diff --check` passed (exit code 0).
- SHA-256 comparisons against pre-report baselines matched for all nine reviewed Phase 5H reports, the six protected files below, and the two existing focused test files. No pre-existing artifact in these checks changed:

| Protected file | SHA-256 |
|---|---|
| `static/js/dashboard.js` | `BA8D9B0A787655A4769534B1A472325665D547B8A23F8F9047CE6BF74AFFD6B6` |
| `static/js/executive-dashboard.js` | `3FA074095B7627B5270ACB9BC127662C74B1C8466C19F57F2A54DB65FB0E22A7` |
| `templates/dashboard/notifications.html` | `AE4B24D71D886BAE080450621C74ED79F427725D9B1D2F59DCBCE881A60F9B42` |
| `templates/layouts/base.html` | `DA6BA2D57384F60B36A266E93A970D9AAD819D21DCEC014D1D104DB65E40BE7D` |
| `templates/layouts/employee_base.html` | `4064A7EB6C70369DEAEB89FFDC95BA5A42661CF4B422C04651415644A1C9619C` |
| `templates/auth/change_password.html` | `66294DBB2B4BAFB15F28FF953719431FAB37D5B749EFE1596998DA1607A99C3B` |
- Final Git status retained the pre-existing changes and untracked files recorded at entry, plus this one new evidence report; no pre-existing work was reverted or rewritten.

## Safest next step

If a new runtime attempt is desired, provide separate, explicit authorization for exactly one attempt and first confirm native Network capture readiness on the QA login tab. Capture only sanitized method, path, timestamps, status, and request IDs, then correlate a tightly bounded QA log window. Do not infer the 401 reason from its status alone, and do not retry if no request is captured.
