# Phase 5H-P — Minimal Password-Change CSRF Fix and Regression Test

Timestamp: 2026-10-09 23:18:28 +06:30 (time supplied for this phase)

## Result

**QA isolation: PASS**  
**Targeted regression tests: PASS (8 passed)**  
**Authentication verdict: BLOCKED — AUTHENTICATION UNVERIFIED**

The previous source review's claim that the password-change page sent no effective CSRF header was incomplete: `static/js/csrf.js` is loaded by the page and wraps same-origin non-safe `fetch` calls to copy the JavaScript-readable `csrf_token` cookie into `X-CSRF-Token`. This phase makes the password-change request explicitly include that same established token and fails closed with a user-visible message if it is missing. It does not change or bypass middleware protections.

## Safety gate

Verified before source inspection or edits:

- Working directory and repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit before edits: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa` were running in Compose project `kpi_management_system_qa`.
- API identity, read only from allowlisted container environment metadata: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- No HTTP request or database connection was made for this gate. No production resource was accessed.

The requested report path did not exist before it was created.

## Existing CSRF contract inspected

- `app/main.py::CSRFMiddleware` creates a JavaScript-readable `csrf_token` cookie when absent, with `HttpOnly=false` and SameSite=Lax. For non-safe cookie-authenticated requests without a bearer header, it requires the cookie and `X-CSRF-Token` header to match; otherwise it returns HTTP 403. The password-change route is not exempt.
- `static/js/csrf.js` reads the same cookie and adds `X-CSRF-Token` to same-origin non-safe `fetch` requests when a token is present.
- `templates/auth/change_password.html` loads `/static/js/csrf.js` before its inline form handler. The password-change request uses same-origin credentials.
- The change-password route and `ChangePasswordRequest` schema were not changed. Existing API validation, password history checks, session revocation, and commit behavior remain intact.

## Implementation

Changed `templates/auth/change_password.html` only in the password-change submit handler:

- Reads the existing `csrf_token` cookie using the application's established cookie name/source.
- Shows “Security token is unavailable. Reload this page and try again.” and returns before sending the request if the cookie is absent or empty.
- Sends the token in the exact `X-CSRF-Token` header expected by middleware.
- Keeps the API URL, JSON body, cookie credentials mode, password checks, and allowlisted post-change redirect unchanged.
- Does not expose access or refresh tokens, log the CSRF token, or weaken/disable CSRF.

Regression tests:

- `tests/test_csrf_middleware.py`: generalized its request helper to select a path, and exercises the actual password-change path for a valid matching cookie/header (passes), missing header (403), missing CSRF cookie (403), and mismatched header (403).
- `tests/test_password_change_csrf.py`: source-level regression assertions verify cookie source, explicit header, missing-token message, endpoint, credentials, request body, and post-success redirect contract.
- Existing validation tests `test_phase42_password_security.py::test_password_change_rejects_bad_current_password_and_confirmation` and `::test_password_change_revokes_sessions_after_policy_validation` were also selected.
- Existing redirect hardening test `test_post_p0_hardening.py::test_first_login_redirect_does_not_depend_on_stored_user_role_or_unsafe_csp` was selected.

## Test execution

Executed only isolated selected tests with:

`python -m pytest -q --disable-warnings tests/test_csrf_middleware.py::test_cookie_mutation_requires_matching_csrf_token tests/test_csrf_middleware.py::test_cookie_mutation_rejects_missing_csrf_cookie tests/test_csrf_middleware.py::test_matching_csrf_token_allows_cookie_mutation tests/test_csrf_middleware.py::test_cookie_mutation_rejects_mismatched_csrf_token tests/test_password_change_csrf.py tests/test_phase42_password_security.py::test_password_change_rejects_bad_current_password_and_confirmation tests/test_phase42_password_security.py::test_password_change_revokes_sessions_after_policy_validation tests/test_post_p0_hardening.py::test_first_login_redirect_does_not_depend_on_stored_user_role_or_unsafe_csp`

Observed result: **8 passed, 1463 warnings in 13.68s**. The warnings were emitted by dependencies/test infrastructure; the command exited successfully. The middleware tests invoke the middleware directly; the selected tests did not start the app lifespan, contact a service, or mutate persistent state. The remainder of the suite was not run.

## Files changed

- Modified: `templates/auth/change_password.html`
- Modified: `tests/test_csrf_middleware.py`
- Added: `tests/test_password_change_csrf.py`
- Added: `security-docs/phase5h-password-change-csrf-fix-20261009.md`

No application API, middleware, configuration, database, auth/session behavior, Docker, dependency, or Phase 5C instrumentation code changed. The five previously approved frontend files were untouched.

## Preservation checks

- Final `git diff --check`: passed.
- Existing reports were not modified. SHA-256 checks of the nine previously listed Phase 5D/5H reports, Phase 5H-N reconciliation report, and Phase 5H-O source-review report matched their values captured before report creation.
- The five approved frontend files' SHA-256 hashes matched their values captured before implementation.
- `app/phase5c_diagnostics.py` remains absent.
- No files were staged or committed.

## Scope confirmation and runtime status

No login attempt, browser interaction, authentication request, password reset/change, recovery or seed command, database query/mutation, account/session/OAuth change, production access, deployment, or performance test occurred. No credentials, hashes, access/refresh tokens, cookie values, or secrets were accessed or written to this report.

These isolated tests demonstrate only the code behavior they exercise. They do not establish whether the prior controlled login succeeded, whether it reached the password-change flow, or whether authentication now works in the running QA browser. The runtime authentication blocker remains **BLOCKED — AUTHENTICATION UNVERIFIED**.
