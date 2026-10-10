# Phase 5H-O — Read-Only Authentication Source-Level Review

Review timestamp: 2026-10-09 22:58:42 +06:30 (time supplied for this phase)

## Verdict

**SOURCE REVIEW COMPLETE — RUNTIME AUTHENTICATION STILL UNVERIFIED**

Authentication blocker: **BLOCKED — AUTHENTICATION UNVERIFIED**  
Runtime diagnosis: **UNDETERMINED**

One source-level integration defect was identified in the conditional first-login password-change flow: the page sends a cookie-authenticated state-changing request without the CSRF header required by middleware. This establishes a code-path failure if that flow is taken; it does not prove that the controlled QA attempt took that path or that this defect caused its outcome.

## QA isolation gate

Gate completed before source inspection:

- Working directory and repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- Expected containers were all `running` and labeled with Compose project `kpi_management_system_qa`: `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, `kpi_scheduler_qa`.
- Allowlisted API container identity fields: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- Limitation: no HTTP or database request was made in this phase. `current_database()` was not re-queried; earlier QA reports record it as `kpi_qa`, but that is not a fresh check for this review.
- The requested report path did not exist before authoring.

The repository was on the expected branch and commit. Existing user changes consisted of the five approved frontend modifications and prior untracked QA reports. No source, test, configuration, or existing report changes were present at pre-review status inspection.

## Scope and actions

This was a read-only source and test inspection. Tests were inspected but **not executed**.

No HTTP or authentication request, browser interaction, login attempt, credential validation, password reset/change, recovery/seed/bootstrap script, database query or mutation, account/session/OAuth mutation, production access, deployment, performance test, package installation, or source/config/test edit occurred.

## Sources inspected

Authentication implementation and configuration:

- [`app/modules/auth/router.py`](../app/modules/auth/router.py)
- [`app/modules/auth/service.py`](../app/modules/auth/service.py)
- [`app/modules/auth/schemas.py`](../app/modules/auth/schemas.py)
- [`app/modules/auth/models.py`](../app/modules/auth/models.py)
- [`app/modules/users/models.py`](../app/modules/users/models.py)
- [`app/core/dependencies.py`](../app/core/dependencies.py)
- [`app/core/security.py`](../app/core/security.py)
- [`app/core/config.py`](../app/core/config.py)
- [`app/main.py`](../app/main.py)
- [`app/middleware/logging_middleware.py`](../app/middleware/logging_middleware.py)
- [`app/web/auth_web.py`](../app/web/auth_web.py)
- [`app/database/qa_seed.py`](../app/database/qa_seed.py)
- [`app/database/qa_recover.py`](../app/database/qa_recover.py)
- [`app/database/bootstrap.py`](../app/database/bootstrap.py)
- [`templates/auth/login.html`](../templates/auth/login.html)
- [`templates/auth/change_password.html`](../templates/auth/change_password.html)
- [`docs/qa-environment.md`](../docs/qa-environment.md)

Relevant tests inspected:

- [`tests/test_auth_service.py`](../tests/test_auth_service.py)
- [`tests/test_api_endpoints.py`](../tests/test_api_endpoints.py)
- [`tests/test_security_helpers.py`](../tests/test_security_helpers.py)
- [`tests/test_phase41_jwt_security.py`](../tests/test_phase41_jwt_security.py)
- [`tests/test_phase42_password_security.py`](../tests/test_phase42_password_security.py)
- [`tests/test_token_type_security.py`](../tests/test_token_type_security.py)
- [`tests/test_csrf_middleware.py`](../tests/test_csrf_middleware.py)
- [`tests/test_logout_no_body_contract.py`](../tests/test_logout_no_body_contract.py)
- [`tests/test_post_p0_hardening.py`](../tests/test_post_p0_hardening.py)
- [`tests/test_qa_seed.py`](../tests/test_qa_seed.py)
- [`tests/test_qa_recover.py`](../tests/test_qa_recover.py)

## Authentication flow map

| Step | Source behavior |
|---|---|
| Login page and request | `app/web/auth_web.py::login_page` renders the page. The submit handler in `templates/auth/login.html` sends same-origin JSON `POST /api/v1/auth/login`, with `credentials: 'same-origin'`; a non-2xx response displays a toast, and a fetch exception displays a network error. |
| Routing and validation | `app/modules/auth/router.py::login` accepts `LoginRequest` (`email`, `password`, optional `remember_me=false`) and applies a 5-per-minute limiter. Pydantic checks field types, but this schema does not normalize the email. |
| Account/password checks | `AuthService.authenticate_user` looks up the exact email with a row lock. Missing or local-password-disabled accounts and bad passwords produce 401; inactive/deleted and currently locked accounts produce 403. An expired lock is cleared and committed. Five failed password attempts set a 30-minute lock. Failure history is recorded for bad passwords. |
| Password verification | `app/core/security.py::verify_password` uses the configured Passlib context (Argon2 default, legacy bcrypt support), rejects over-limit input and bcrypt inputs above 72 bytes, and does not expose stored hashes. |
| Successful login/session | On valid credentials, the service clears failure/lock state, may set `must_change_password` for expired credentials, writes success history/audit, then calls `issue_authenticated_session`. With a real SQLAlchemy session it persists an `AuthSession`, including expiry, revocation state, and a digest of the refresh token; session expiry is one day without Remember me or configured refresh-token days with it. Device/session limits can revoke older sessions. |
| JWTs | The service issues access and refresh JWTs with subject, user ID, session ID, role/level; `security._create_token` supplies type, expiry, issued-at, and JTI. `decode_token` checks signature/algorithm, expiry via JWT decoding, required claims, expected token type, and (when requested) session claims. The implementation does not set issuer or audience claims. |
| Login cookies | `router.py::login` sets `access_token` and `refresh_token` as HttpOnly, SameSite=Lax cookies. `Secure` is always true in production; outside production it depends on request/forwarded-proto and host/scheme logic in `cookie_secure_for_request`. Max-Age is set only when Remember me is enabled. The call does not explicitly pass Path or Domain, so it relies on framework defaults. The JSON response also contains the token fields per `TokenResponse`. |
| Login redirect | On a successful login response, the page redirects to `/auth/change-password?next=...` if `must_change_password` is true; otherwise it stores user profile data and redirects to a role-derived dashboard or one of three allowlisted `next` paths. Error responses remain on the login page. |
| First-login password change | The page sends `POST /api/v1/auth/change-password` with the old/new/confirmation password fields and credentials included. The endpoint requires `get_current_user`, verifies the current password, validates confirmation, password policy and history, stores the new hash, sets `must_change_password=false`, revokes the user's authentication sessions, then commits. After success the page redirects back to login with a safe `next` value so a fresh login/session can be established. |
| `/api/v1/auth/me` | `get_current_user_context` depends on `get_current_user`, then serializes current user/role/permission/department/position data. |
| Refresh/logout | `refresh_token` accepts the refresh cookie or body token, checks blacklist and JWT/session claims, requires active user and matching live session/hash, blacklists the old refresh token, rotates it, and resets cookies. `logout` blacklists presented access/refresh token digests, revokes the referenced session when it can decode a session ID, and clears both auth cookies. |

### Cookie, CSRF, and first-login finding

`CSRFMiddleware` in `app/main.py` exempts the login and Swagger-login paths. For other non-safe methods, when an `access_token` cookie is present and there is no bearer header, it requires both a `csrf_token` cookie and a matching `X-CSRF-Token` header; otherwise it returns HTTP 403.

The normal password-change page's fetch includes `Content-Type` and `credentials: 'include'`, but does **not** include `X-CSRF-Token`. After a successful local login, the browser has the newly issued `access_token` cookie. If `must_change_password=true`, the next password-change POST is therefore cookie-authenticated and is expected to be rejected by this middleware with 403 before reaching the endpoint. The page then shows the error toast and does not redirect back to login.

This is a source-level mismatch between [`app/main.py`](../app/main.py) (CSRF check) and [`templates/auth/change_password.html`](../templates/auth/change_password.html) (request headers). It is not evidence that the Phase 5H-M account had `must_change_password=true`; the prior read-only status snapshot recorded it false at its observation time, and the controlled attempt's login response flag was unavailable.

### `/api/v1/auth/me` HTTP 401 branches

`app/core/dependencies.py::get_current_user` extracts the access token in this order: bearer token recognized by the OAuth2 dependency, explicit Authorization bearer header, then `access_token` cookie. Its 401 branches are:

1. No token: `Not authenticated. Token missing.`
2. JWT decode/type/claim/UUID failure, including expired/invalid token or missing required session claims: `Token expired or invalid.`
3. Access-token digest present in `TokenBlacklist`: revoked-token 401.
4. No `AuthSession` matching session ID and user ID with `revoked_at IS NULL` and future expiry: revoked/expired-session 401.
5. User lookup by subject returns no row or the account is deleted/inactive: user-not-found/disabled 401.

A locked user is rejected by a separate HTTP 403 branch. `/me` itself does not determine which dependency branch caused a 401. Refresh has its own missing, invalid/expired, blacklisted, inactive/mismatched-user, and missing/revoked session 401 cases.

## Relevant test coverage and gaps

All entries below were **inspected, not executed during this phase**.

| Test | Assertion coverage | Coverage gaps for this blocker |
|---|---|---|
| `test_auth_service.py::test_authenticate_user_returns_tokens_for_valid_credentials`; `::test_authenticate_user_rejects_unknown_email` | Service success payload and unknown-email 401, using a fake DB and patched password/token helpers. | Does not exercise real persisted session creation, cookie headers, incorrect-password history/lockout, inactive/deleted/locked users, or first-login behavior. |
| `test_api_endpoints.py::test_login_route_returns_tokens`; `::test_me_route_returns_current_user_context` | Login returns a 200 JSON token payload under mocked helpers; `/me` response serialization is tested with `get_current_user` overridden. | Login test does not assert Set-Cookie attributes and does not traverse the real `/me` authentication dependency; `/me` test bypasses token/session validation. |
| `test_security_helpers.py::test_password_hash_round_trip`; `::test_access_and_refresh_tokens_include_expected_claims` | Hash/verify round trip and basic token subject/type claims. | Does not test browser cookies, DB session, blacklist or `/me` branches. |
| `test_phase41_jwt_security.py::test_tokens_include_server_controlled_claims_and_validate_by_type`; `::test_expired_or_missing_subject_access_tokens_are_rejected`; `::test_invalid_signature_and_missing_session_claims_are_rejected`; `::test_access_endpoint_rejects_refresh_token_and_refresh_endpoint_rejects_access_token`; `::test_refresh_rotation_replay_and_logout_revocation`; `::test_password_change_revokes_existing_sessions` | Token claim/type/signature checks, refresh rotation/replay and revocation, and session-revocation call on password change. | Does not cover all real DB-backed `/me` blacklist/session/account branches or login cookie issuance. |
| `test_token_type_security.py::test_refresh_token_cannot_authenticate_as_access_token` | Refresh token is not accepted as an access token. | Does not cover cookie extraction or session persistence. |
| `test_phase42_password_security.py::test_password_change_rejects_bad_current_password_and_confirmation`; `::test_password_change_revokes_sessions_after_policy_validation` | Password checks and endpoint-side update/revoke behavior with fakes. | Does not exercise middleware around the endpoint or the actual HTML page request headers. |
| `test_csrf_middleware.py::test_cookie_mutation_requires_matching_csrf_token`; `::test_matching_csrf_token_allows_cookie_mutation`; `::test_bearer_api_mutation_is_not_blocked_by_csrf` | Generic cookie-authenticated mutation is rejected without matching CSRF token and accepted with it; bearer behavior is covered. | Uses `/api/v1/tasks`; does not assert login exemption or verify the password-change template sends the required header. |
| `test_post_p0_hardening.py::test_first_login_redirect_does_not_depend_on_stored_user_role_or_unsafe_csp` | Static source assertions around safe post-login destinations and login/password-page behavior. | Does not run a first-login browser/API flow and does not assert CSRF header integration. |
| `test_logout_no_body_contract.py::test_logout_without_request_body_revokes_session_and_clears_cookies` | Logout revokes session, blacklists tokens, and emits cookie-deletion headers. | Does not prove login cookie issuance or browser acceptance. |
| `test_qa_seed.py::test_qa_seed_requires_qa_environment`; `::test_qa_seed_rejects_remote_database`; `::test_qa_fixture_weights_include_timeliness` | QA seed environment/target guards and fixture metadata. | Does not test whether an existing user's password hash changes or remains stable. |
| `test_qa_recover.py::test_qa_environment_is_accepted`; `::test_non_qa_or_wrong_database_is_rejected`; `::test_non_allowlisted_email_is_rejected`; `::test_valid_qa_reset_changes_only_target_password_state` | Recovery target guards, allowlist/role checks, and scoped target mutation using a fake session. | Recovery was not executed; tests do not inform whether it affected the incident account. |

No inspected test demonstrates an end-to-end normal login with a real persisted session, verifies login `Set-Cookie` flags, tests the complete browser `/me` round trip, or catches the CSRF/password-change integration mismatch.

## QA seed and startup observations

- `qa_seed._get_or_create` applies supplied `values` only when it creates a row. In `seed_qa`, the existing-user branch updates role, position, active, locked, and `must_change_password` fields, but does not replace `hashed_password`. Therefore, if the QA seed is manually rerun for an existing user, the previous password hash is retained while selected account flags are reset.
- `docs/qa-environment.md` documents that the broad QA seed is a guarded explicit operation, not normal automatic startup. `app/main.py` calls `bootstrap_initial_super_admin`; that bootstrap creates an account only if configured and no Super Admin exists, otherwise skips. It is separate from `seed_qa`.
- This is a possible credential-drift mechanism, not a finding that the seed ran for the target, that it retained the target's hash in the relevant period, or that this caused a rejected login. No seed/recovery/bootstrap command was executed and no password hash was read.
- The recovery implementation is limited to `qa.superadmin@example.test`, requires QA host/database/port and role checks plus operator confirmation, sets `must_change_password=true`, records password history, and revokes active sessions and linked OAuth rows. It was inspected only.

## Existing runtime evidence versus source behavior

The prior reports [`phase5h-auth-verification-20261009.md`](phase5h-auth-verification-20261009.md), [`phase5h-auth-log-correlation-20261009.md`](phase5h-auth-log-correlation-20261009.md), [`phase5h-auth-root-cause-diagnosis-20261009.md`](phase5h-auth-root-cause-diagnosis-20261009.md), [`phase5h-controlled-auth-verification-20261009.md`](phase5h-controlled-auth-verification-20261009.md), and [`phase5h-auth-evidence-reconciliation-20261009.md`](phase5h-auth-evidence-reconciliation-20261009.md) were consulted.

They record older `/api/v1/auth/me` 401 events, no reliable login-to-`/me` correlation, unavailable response-cookie and request-cookie metadata, and inconsistent observations of the login page versus the operator's report that login continued normally. An earlier QA state snapshot found the account active, unlocked, `must_change_password=false`, and no active persisted session at that snapshot time. None of these observations establishes the state at the Phase 5H-M attempt. The access logger records method, path, status, duration, and request ID, but not login success, `must_change_password`, or cookie delivery.

Source can enumerate failure branches and the conditional CSRF defect above. It cannot establish whether the attempt reached the application, whether the intended credential matched the stored hash, what cookies were emitted/stored/sent, which `/me` branch ran, whether the password-change redirect occurred, or whether that defect was exercised. Runtime root cause remains **UNDETERMINED**.

## Hypothesis matrix

Classifications describe what source and retained evidence support; they do not claim an incident root cause.

| Hypothesis | Classification | Evidence boundary |
|---|---|---|
| Login request never reached the application | `NOT ASSESSABLE FROM AVAILABLE EVIDENCE` | No reliable request record correlates to the controlled attempt. A missing log in a bounded window is not proof of non-arrival. |
| Login reached the application but credentials were rejected | `PLAUSIBLE BUT UNVERIFIED` | The service has 401 branches for missing account/local password/bad password and records wrong-password attempts, but no correlated login response or history row establishes one. |
| Login succeeded but the browser did not store auth cookies | `PLAUSIBLE BUT UNVERIFIED` | Source sets two auth cookies after service success; no attempt-specific response headers or browser exclusion evidence exists. |
| Cookies were stored but not sent to `/api/v1/auth/me` | `PLAUSIBLE BUT UNVERIFIED` | `/me` reads bearer/header then `access_token` cookie; no correlated request-cookie names are retained. |
| Cookies were sent but JWT validation failed | `PLAUSIBLE BUT UNVERIFIED` | Decode/signature/expiry/type/claim/UUID errors produce 401; no error category for the attempt is available. |
| JWT validation succeeded but persisted-session validation failed | `PLAUSIBLE BUT UNVERIFIED` | `/me` requires a matching unrevoked, unexpired `AuthSession`; no attempt-correlated session lookup or `/me` branch evidence exists. |
| Account eligibility or lockout rejected login | `PLAUSIBLE BUT UNVERIFIED` | Source has inactive/deleted and active-lock 403 paths; an older account snapshot cannot establish state at attempt time. |
| First-login password-change flow was mishandled | `SUPPORTED BY SOURCE` | Conditional source-level mismatch: password-change POST lacks required CSRF header while middleware requires it for a cookie-authenticated POST. Whether the controlled attempt had `must_change_password=true` is unverified. |
| QA seed retained an old hash for an existing account | `SUPPORTED BY SOURCE` | The seed code leaves an existing hash untouched; whether the seed ran for this account or caused the incident is unverified. |
| Browser tooling could not observe required Network/cookie evidence | `SUPPORTED BY SOURCE` | Prior inspection/reconciliation reports consistently document that the available shared-browser tooling did not expose retained request/response headers or cookie metadata. |
| Another explanation: request validation or login rate limit | `SUPPORTED BY SOURCE` | The request schema can reject malformed/missing fields, and login has a five-per-minute limiter; no correlated status shows either occurred. |

## Minimal safe next step

Treat the CSRF/password-change mismatch as a separate, reproducible source-level issue: obtain separate authorization for a minimal fix and an integration regression test that exercises the actual cookie-authenticated password-change request. Do not treat such a fix as proof of the Phase 5H-M runtime cause or as authentication verification. Any new QA authentication flow requires separate explicit authorization.

## Final preservation verification

- `git diff --check`: passed after report creation.
- Repository remains on `main` at `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`.
- The only tracked diff filenames remain the five pre-existing approved frontend files; no application source, tests, configuration, Docker, or dependency files changed.
- SHA-256 checks of the nine existing QA reports listed in the phase request and the existing Phase 5H-N reconciliation report matched their values captured before report creation.
- SHA-256 checks of all five approved frontend files matched their pre-review values.
- `app/phase5c_diagnostics.py` remains absent; no Phase 5C instrumentation was introduced.
- The only new file in this phase is `qa-evidence/phase5h-auth-source-review-20261009.md`.
- Nothing was staged or committed. No existing report was modified.
- No HTTP/authentication request, login attempt, browser interaction, database mutation, credential reset, production access, deployment, performance test, or test execution occurred.
