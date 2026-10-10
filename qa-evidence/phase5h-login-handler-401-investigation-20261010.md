# Phase 5H-S9 — Login Handler State and HTTP 401 Read-Only Investigation

Investigation time: 2026-10-10 00:35:28 +06:30 (Asia/Yangon)

## Verdict

**CLIENT CLEANUP LOGIC APPEARS CORRECT — HISTORICAL STATE UNPROVEN**

**Secondary finding:** the historical login HTTP 401 reason is unproven. The available S6 QA access log records the login route and status, but no browser Network response, response body, or direct browser-side request-ID correlation is available.

This phase used source and existing reports only. No browser interaction or runtime request was made.

## QA safety gate

- Repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- HEAD: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Proposed report path was absent before creation.
- Docker context: `desktop-linux`
- The five approved QA containers were running under Compose project `kpi_management_system_qa`:
  - `kpi_api_qa`
  - `kpi_postgres_qa`
  - `kpi_redis_qa`
  - `kpi_minio_qa`
  - `kpi_scheduler_qa`
- Allowlisted API identity: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- No production resource was inspected or contacted.

## Source files inspected

### Login template and handler

- [templates/auth/login.html](../templates/auth/login.html), lines 187–233: one `loginForm`; required email and password inputs; `loginSubmitBtn` has no initial `disabled` attribute.
- Same file, lines 288–290: script references for the CSP event bridge, shared CSRF fetch wrapper, and notifications.
- Same file, lines 347–356: password-toggle listener and matching input/icon IDs.
- Same file, lines 360–380: a separate forgot-password handler targets its own button; it does not disable the login submit button.
- Same file, lines 382–384: remembered-email initialization targets the matching email field; it does not disable the submit button.
- Same file, lines 388–398: OAuth buttons are separately disabled during OAuth navigation; these are not the login submit button.
- Same file, lines 418–436: one login form submit listener; it reads the matching form controls, changes the button label, disables the button, then starts an awaited same-origin POST to `/api/v1/auth/login` with `credentials: 'same-origin'`.
- Same file, lines 438–471: success navigation or non-OK/error notification handling.
- Same file, lines 472–475: `finally` restores the button label and sets `disabled = false`.
- Same file, lines 52–62: disabled-state CSS affects appearance/cursor only; it does not disable the button.

### Routes, middleware, and dependencies

- [app/web/auth_web.py](../../app/web/auth_web.py), lines 9–15: GET `/auth/login` renders `auth/login.html`.
- [app/modules/auth/router.py](../../app/modules/auth/router.py), lines 118–119: GET `/api/v1/auth/me` depends on `get_current_user`.
- Same file, lines 168–189: POST `/api/v1/auth/login` calls `AuthService.authenticate_user`; access/refresh cookies are set only after that call succeeds.
- [app/main.py](../../app/main.py), lines 265–296: CSRF middleware protects cookie-authenticated mutations but explicitly exempts `/api/v1/auth/login`. A CSRF rejection in this middleware would be HTTP 403, not the observed 401.
- [app/core/dependencies.py](../../app/core/dependencies.py), lines 180–267: authentication dependency resolves bearer/cookie access tokens and can reject missing, invalid, revoked, expired, disabled, or locked sessions/users. These checks apply to authenticated routes such as `/me`, not the login POST.
- [app/main.py](../../app/main.py), lines 152–169: HTTP exception handler returns non-5xx exception details as JSON; the historical response body was not retained in the reviewed evidence.
- [app/middleware/logging_middleware.py](../../app/middleware/logging_middleware.py): request IDs, method/path/status, and processing time are attached/logged; it does not record the authentication response body.

### Focused tests and error paths

- [tests/test_auth_service.py](../../tests/test_auth_service.py), lines 68–76: unit test demonstrates an unknown email can produce HTTP 401.
- [tests/test_api_endpoints.py](../../tests/test_api_endpoints.py), lines 51–87: route test covers successful login with mocked service dependencies.
- [tests/test_csrf_middleware.py](../../tests/test_csrf_middleware.py), lines 32–101: tests exercise CSRF behavior for cookie-authenticated mutations and bearer requests; the login path exemption is source-confirmed in middleware.
- [tests/test_password_change_csrf.py](../../tests/test_password_change_csrf.py): source-contract test for the password-change template, not the login button lifecycle.
- No focused test inspected here asserts browser login button state after a 401, `finally` execution, or navigation races. No tests were run in this investigation.

## Handler lifecycle and cleanup analysis

1. Native browser constraint validation applies to the required inputs before a form `submit` event. Empty/invalid required fields can prevent the handler from running; that alone does not set this button disabled.
2. When the listener runs, it reads values and element references, saves the original label, and sets the loading label and `disabled = true`.
3. The `try` begins immediately after the disable assignments. It includes construction and awaiting of `fetch`, response JSON parsing, success navigation, and non-OK error handling.
4. A fulfilled fetch with HTTP 401 follows the non-OK branch, awaits `response.json()`, then reaches `finally`. A fetch rejection or JSON parsing/other exception inside `try` follows `catch` and then `finally`.
5. On those ordinary settled paths, `finally` restores the label and enables the button. There is no explicit `return` between disable and cleanup, no second login-form submit listener was found, and all referenced login form/control IDs match the template.
6. A pending fetch keeps execution at the `await` and leaves the button disabled until it settles. `finally` cannot run if the document execution context is destroyed before continuation, such as an external navigation/process teardown; this possibility is not demonstrated for the historical attempt. A `window.location` assignment initiates navigation within `try`; while JavaScript normally continues synchronously to `finally`, no source-only review can guarantee cleanup if the execution context is torn down.
7. Statements that read the controls, set the label, and disable the button precede the `try`. A synchronous exception during those operations could reject the async event callback before entering `try`; however, the S7 “Signing in…” state shows that the disable assignment had occurred at some time, and no direct evidence identifies such an exception.
8. The CSP bridge submit listener only handles elements with `data-csp-onsubmit`; the login form does not use that attribute, so it does not duplicate the login handler.

**Conclusion on S7 state:** the source's ordinary in-flight/401 cleanup logic appears correct. The S7 disabled state is consistent with an unresolved pending request or stale client/page execution state, but the historical page was not available for direct state inspection in S9. The S6 access-log completion does not prove that the browser received/processed the response or executed `finally`. Cause of the later disabled UI state therefore remains unproven.

## Historical HTTP 401 evidence

The existing [Phase 5H-S6 runtime report](./phase5h-controlled-auth-runtime-evidence-20261010.md) records:

- `POST /api/v1/auth/login`
- HTTP 401
- QA API request ID `3ae657f8-d339-4875-b55a-7618bce1d892`
- Server processing time `15892.95ms`
- Timestamp `2026-10-09T17:52:38.474Z`
- No `/api/v1/auth/me` access-log record in the bounded window examined for S6.

This log record establishes that a POST to the login path was processed far enough for the request logging middleware to record its response status. Its temporal placement associates it with the S6 attempt window, but the native browser Network event and response `X-Request-ID` were unavailable; exact browser-to-log request-ID correlation was not independently verified.

The 401 alone does not identify the rejection reason. Source shows the login service can return 401 for an unknown email, a disabled local-password method/no stored local password, or an incorrect password; disabled or locked-account paths can return 403. The login route is CSRF-exempt and the CSRF middleware's failure status is 403, so the observed 401 is not evidence of a CSRF rejection by that middleware. The exact response body was not captured, and no account/database state was queried in this phase. Thus invalid credentials versus another 401 authentication-policy path remains unknown.

The S7 report records no matching login or `/me` request in its own bounded window. That absence is not proof that a browser request was never sent. The S8 report records that the later shared tab was on `/superadmin/dashboard`, but its URL alone is not proof that S6 or S7 authenticated successfully.

## Verified facts versus hypotheses

**Verified from source/reports**

- The submit button is enabled by default in the template.
- The login handler disables it before beginning the fetch and re-enables it in `finally`.
- Normal HTTP 401 and fetch/JSON errors inside the `try` are covered by `finally`, provided the awaited operation settles and the execution context continues.
- S6's QA API log reports the login POST as HTTP 401 with a request ID and processing time.
- Login is exempt from this CSRF middleware; the middleware's CSRF failure is 403.
- No `/me` status or authenticated identity was captured for S6.
- S7 had a disabled-button timeout but no matching auth entry in its bounded log window.

**Not established**

- Whether S6's browser received and processed its HTTP 401 response.
- Whether the S7 disabled state was a still-pending handler, stale document state, navigation/execution-context issue, or another browser-side cause.
- The exact reason for S6's 401.
- Whether any attempt established a session or authenticated identity.

## Limitations

- No browser was opened, read, or interacted with in S9.
- Native DevTools Network history and the S6 response body were unavailable.
- No new runtime log query, HTTP request, database query, or test execution was performed.
- Historical correlation is based on the existing S6 report and its bounded QA log record, not a new independently captured browser event.
- No application or test behavior was modified.

## Safest next action

Do not change code or credentials based on the disabled state or the 401 alone. If the user separately authorizes further runtime diagnosis, first obtain operator-controlled sanitized Network evidence for one explicitly authorized attempt and correlate the browser request ID with a bounded QA log. Preserve the current browser state unless separately authorized to navigate.

## Final integrity and restrictions

- No browser interaction, authentication request, credential operation, account/session change, database operation, production access, or deployment occurred.
- Existing Phase 5H reports and protected files were not modified.
- `app/phase5c_diagnostics.py` remains absent; no instrumentation was added.
- No files were staged, committed, reset, stashed, or discarded.
- Initial Git status was recorded before source review. The final status and hash checks are reported in the session response.
