# Phase 5H-K — QA Authentication Root-Cause Diagnosis

Date: 2026-10-09

## Verdict and scope

**BLOCKED — AUTHENTICATION UNVERIFIED**

Confirmed root cause: **UNDETERMINED**.

This phase was read-only. No authentication request, browser interaction, database mutation, test, performance measurement, production access, or deployment occurred. No credentials, hashes, token material, cookies, or connection strings were accessed or emitted.

## QA isolation

The isolation gate passed before inspection:

- Repository: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- All required containers report Compose project `kpi_management_system_qa` and state `running`:
  - `kpi_api_qa`
  - `kpi_postgres_qa`
  - `kpi_redis_qa`
  - `kpi_minio_qa`
  - `kpi_scheduler_qa`
- Sanitized API configuration: `ENV=qa`, host `postgres-qa`, port `5432`, database `kpi_qa`; `SKIP_DATABASE_SEED=true`; `SKIP_DATABASE_MIGRATIONS=true`.
- PostgreSQL `current_database()`: `kpi_qa`.
- No health request was made. The previously recorded QA health gate returned HTTP 200.
- No production host or resource was contacted.

## Evidence matrix

| Area | Evidence available | Finding | Confidence |
|---|---|---|---|
| QA isolation | Container labels/states, selected non-secret API config, and PostgreSQL database identity | QA project and DB target match requirements | High |
| Account status | Read-only query for the allowlisted account | Present as Super Admin; active; unlocked; local password enabled; `must_change_password=false` at query time | High for current snapshot; not historical proof |
| Login history | Read-only aggregate for the allowlisted account | 167 records total (110 successful, 57 failed); latest record at `2026-10-09 07:11:18.809925` UTC; zero records since `08:00Z` | High for queried DB state |
| Auth sessions | Read-only aggregate | 110 total; 0 currently active; 110 revoked; 87 expired (expired is an overlapping condition); latest `last_used_at` at `09:11:34.698116` UTC | High for current snapshot; no request/session correlation |
| Password history | Read-only aggregate; no hashes read | 11 history entries; latest at `2026-10-09 02:55:57.232277` UTC | High for aggregate; event attribution unknown |
| Password/seed lifecycle | Source and QA docs | QA seed supplies a password hash only as create-time values; existing users' password hashes are not updated. Seed is skipped at startup; manual QA seed sets existing-account flags `active=true`, `locked=false`, `must_change_password=false`. | High for code behavior; whether seed ran is unknown |
| Recovery lifecycle | Recovery implementation and docs | Exact account/QA target allowlisted; recovery sets local password, sets `must_change_password=true`, records history, and revokes active auth sessions and OAuth links. Recovery was not run in this phase. | High for code behavior |
| Cookie issuance/delivery | Router/source only; no retained response/request headers | Login route sets HttpOnly access and refresh cookies after service authentication; browser receipt and subsequent sending are unverified. Local QA loopback normally yields non-Secure, SameSite=Lax cookies; no explicit Domain/Path is supplied. | High for source behavior; runtime attempt unverified |
| JWT/session validation | Source and existing 401 logs | `/me` can return 401 for missing token, invalid/expired/wrong-type JWT or claims, blacklisted token, missing/revoked/expired persisted session, or missing/inactive user. 401 logs do not identify which branch ran. | High for source branches; incident branch unknown |
| Login-attempt correlation | Existing logs and reports | No login or `/me` access-log entries appeared in the bounded `08:00–10:00Z` window. The DB has no login-history entry since `08:00Z`. The exact authorized attempt has no reliable request ID or retained Network entry. | High that records were absent from the queried sources/window; attempt correlation unavailable |
| Performance readiness | No successful current authenticated identity | Authentication has not been verified | High: do not resume |

### Sanitized log and database correlation

The existing earlier records include a reported login POST HTTP 200 at approximately `07:11:19Z` and a `/api/v1/auth/me` HTTP 401 at `07:11:29Z`. The read-only account query's latest successful login-history timestamp is `07:11:18.809925Z`, consistent in time with that earlier login. Login history does not carry an HTTP request ID, so this is temporal consistency, not a definitive request-level join. It is not the later operator-authorized attempt.

The API access middleware logs request method, path, status, duration, and request ID, not credential-verification outcome, `must_change_password`, cookie issuance, or cookie delivery. The later controlled-attempt window has no matching access entries in the retained logs queried. The browser tool did not expose the relevant Network headers.

The current DB snapshot shows zero active sessions and all 110 retained sessions revoked. This means any presented token whose session is among those revoked would fail session validation now. It does not establish that a particular cookie was sent, that its session was one of these rows, or that session rejection caused the earlier 401. The latest session-use time cannot be correlated to the login event without a session identifier, which was not queried or emitted.

## Source control-flow findings

- `app/modules/auth/service.py`: account-not-found, disabled/deleted, locked, local-password-disabled, and password-verification branches occur before successful login recording/session issuance. Active lock expiry can clear lock state during login; no such code was executed here. For a credential verification result, the service records success or failure and commits it; success then issues an authenticated session.
- `app/modules/auth/service.py`: with a real SQLAlchemy session, `issue_authenticated_session` creates a session row, flushes it to obtain its ID, issues JWTs containing session claims, stores a digest of the refresh token, and commits.
- `app/modules/auth/router.py`: after successful service authentication, `/api/v1/auth/login` sets HttpOnly `access_token` and `refresh_token` cookies. Its JSON result carries `must_change_password`.
- `templates/auth/login.html`: on a successful login response, the UI redirects to `/auth/change-password` if the flag is true; otherwise it navigates to a role-selected dashboard. A 4xx login response stays on the login page and displays a toast.
- `app/core/dependencies.py`: `/me` reads a bearer token or access cookie; checks token claims/type/expiry, blacklist, a non-revoked non-expired `AuthSession` matching token claims, and an active non-deleted user. Missing/invalid/blacklisted/session-missing/user-missing cases yield 401; locked user yields 403.
- `app/core/security.py`: JWT decode verifies signature, expiration, required claims and expected token type; the access flow also requires session/user claims.
- `app/database/qa_seed.py`: for a new user it hashes `QA_SEED_PASSWORD`; for an existing user, the supplied create-values (including the password hash) are not applied. It does update role/position and active/locked/must-change flags for existing accounts.
- `docker-compose.qa.yml` and the API's selected environment show `SKIP_DATABASE_SEED=true`; `docs/qa-environment.md` documents that the guarded QA seed is an explicit manual action. `app/main.py` calls initial Super Admin bootstrap, which skips creation if a Super Admin already exists; it does not reset an existing account password.
- `app/database/qa_recover.py`: the reviewed recovery is limited to the exact QA account and QA DB, but changes password state, password history, and revokes auth sessions and OAuth links. This phase did not execute it.

## Ranked hypotheses (not confirmed causes)

1. **The later controlled login did not reach the login service or did not reach a recordable credential-verification outcome.** This best matches the absence of an access-log entry and login-history row in the queried window. However, log retention/window limits or an earlier account rejection branch could also explain the absence; therefore this is a hypothesis, not a confirmed root cause.
2. **The older `/me` 401 followed a successful login but did not pass token/session validation.** The earlier login HTTP 200 and same-second success-history row support that credential verification succeeded for that older event. They do not distinguish cookie delivery from JWT, blacklist, or persisted-session checks, and the current all-revoked session snapshot is not a historical correlation.
3. **The credential used may not match the password currently stored for the QA account.** This is plausible because the QA seed does not update an existing account's hash when its password environment value changes, and startup skips that seed. No password comparison or hash inspection was performed, and there is no evidence which password source the operator used; confidence is low.
4. **A first-login/password-change redirect may explain a login navigation.** Recovery sets `must_change_password=true`, whereas the current snapshot is false; a later password change, explicit seed run, or other state transition could explain the present value. No correlated response flag or page-navigation record exists, so this is unverified.

These hypotheses do not justify changing application code, resetting the account, or changing session state. Overall diagnosis remains **UNDETERMINED**.

## Minimum next action

The highest-value safe evidence is a read-only review of the exact existing login and `/me` Network entries, if retained, capturing only status, cookie names/attributes, explicit cookie exclusion reason, whether cookie names were sent, redirect destination, and sanitized `/me` error category. Do not create new traffic.

If those entries are unavailable, the minimum next step is a separately authorized operator decision for a new controlled QA authentication flow, including whether the operator may complete the password-change flow. No such request or password-change operation is authorized by this report. Any future password recovery/change affects QA password history and revokes sessions; it must use the documented guarded process and direct operator entry, with explicit approval. A session repair would require specific evidence, an explicitly scoped QA action, pre-change snapshot/backup, and a documented restoration path. Performance resumption requires a separate authorization after normal authentication is verified by `/api/v1/auth/me` HTTP 200 and the expected identity.

## Preservation and final checks

- `git diff --check`: passed.
- Branch/commit remain `main` / `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`.
- The five approved frontend modifications remain the only tracked diff filenames.
- The five earlier Phase 5H reports retain their recorded SHA-256 hashes:
  - `qa-evidence/phase5d-performance-baseline.md`: `68B72CAEE9BE75A20E43EFD0E8DE3265BE677BCB0E8547205F70A568B6EE7937`
  - `qa-evidence/phase5h-performance-baseline-20261009-1158.md`: `6C131DA132E1709F431A1E1C9B0C8C6A6CD57856921920B09C00AA8C48CED42D`
  - `security-docs/phase5h-auth-verification-20261009.md`: `7C99851D3BF0B6E3C205F9888427B2A7D303FF9D4501A70B5D7F10FF4A426E97`
  - `security-docs/phase5h-auth-log-correlation-20261009.md`: `6BD375D7D10383344B1ABEBC95F1AF6F9FCEAD951ADA380D6E7846C46E8FCB49`
  - `security-docs/phase5h-auth-blocker-closure-plan-20261009.md`: `42EDEA0A787655A4769534B1A472325665D547B8A23F8F9047CE6BF74AFFD6B6`
- `app/phase5c_diagnostics.py` remains absent.
- The Phase 5H-I network-history report and other earlier reports were read; none was modified.
- No source/configuration/test file changed. This report is the only new file from this phase.
- No authentication request, password change, session mutation, database write, test, benchmark, production access, or deployment occurred. Database inspection used read-only transactions.
