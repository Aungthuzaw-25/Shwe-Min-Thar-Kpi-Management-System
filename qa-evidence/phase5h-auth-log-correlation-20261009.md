# Phase 5H-G — Read-Only QA Authentication Log Correlation

Date: 2026-10-09

## Verdict

**BLOCKED — AUTHENTICATION UNVERIFIED**

Supported classification: **UNDETERMINED**

## QA safety gate

Pre- and post-inspection checks passed:

- Repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- Required containers running in Compose project `kpi_management_system_qa`: `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa`
- API identity: `ENV=qa`, host `postgres-qa`, port `5432`, database `kpi_qa`
- PostgreSQL `current_database()`: `kpi_qa`
- `http://127.0.0.1:18000/health`: HTTP 200
- No production URL, credentials, or resources were used.

## Existing QA log correlation

The API logs were filtered to authentication login and `/me` access-log entries over the bounded UTC window `2026-10-09 00:00:00` through `08:31:00`. Only method, route, status, timestamp, and request ID were emitted.

Existing `/api/v1/auth/me` 401 entries in that window:

| Timestamp (UTC) | Request ID | Route | Status |
|---|---|---|---:|
| 2026-10-09 05:28:42.144950394Z | `4b672aeb-247c-458b-9469-f3f0f8b6a399` | GET `/api/v1/auth/me` | 401 |
| 2026-10-09 05:59:28.704639751Z | `538ddd8c-d177-4638-a4a5-69bfa54405a2` | GET `/api/v1/auth/me` | 401 |
| 2026-10-09 07:11:29.063878788Z | `48dcf46c-6fda-4e71-bc39-512f34f5c372` | GET `/api/v1/auth/me` | 401 |

No `/api/v1/auth/login` access-log entry was found in this window. Prior session evidence described a login POST 200 at approximately `07:11:19Z`, followed by a login-page GET and the `07:11:29Z` `/me` 401. Those earlier events precede the Phase 5H-F operator-authorized attempt and do not establish the outcome of that later attempt.

The Phase 5H-F browser review exposed only a `/me` resource entry, without a reliable new timestamp, login POST status, cookie metadata, or request correlation ID. Therefore it cannot be tied to the Phase 5H-F attempt.

The request logging middleware in `app/middleware/logging_middleware.py` logs method, path, status, duration, and request ID. It does not log login-handler success, `must_change_password`, cookie issuance, or cookie delivery. Server logs cannot prove that the browser stored or sent the cookies.

## Read-only QA database correlation

A read-only transaction queried only the allowlisted account and aggregates from `login_history` and `auth_sessions` during `08:00:00–08:31:00Z`, covering the controlled attempt window. It returned:

- Account found; currently active; currently not locked; currently `must_change_password=false`.
- Login-history rows in that window: 0 (successful: 0, failed: 0).
- Authentication-session rows in that window: 0 (active: 0).

No credential hashes, token material, session IDs, or unrelated user rows were retrieved. These are current read-only observations, not proof of the browser’s cookie state. The absence of matching rows means this query cannot confirm that the login handler successfully authenticated the account or persisted a session during the window. No reliable database correlation to the browser’s `/me` request was available.

## Source correlation

- `app/modules/auth/service.py`: login checks account active/deleted and locked status, verifies the local password, records successful/failed login history, and on success calls `issue_authenticated_session`.
- `app/modules/auth/service.py`: persisted session creation is conditional on a real SQLAlchemy session; the session is flushed before JWT creation and committed with a digest of the refresh token.
- `app/modules/auth/router.py`: JSON login sets HttpOnly `access_token` and `refresh_token` cookies after authentication. The response also includes `must_change_password`.
- `templates/auth/login.html`: a successful response with `must_change_password=true` redirects to `/auth/change-password`; otherwise it redirects to a dashboard. Neither response status nor this flag was logged for the Phase 5H-F attempt.
- `app/core/dependencies.py`: `/me` can return 401 for a missing token, an invalid/expired JWT or missing session claims, a blacklisted access token, a missing/revoked/expired persisted session, or a missing/inactive account. A locked account follows a 403 branch. The observed 401 does not identify which branch ran.

The current database status (`active=true`, `locked=false`, `must_change_password=false`) does not distinguish why any earlier `/me` request returned 401. No cookie delivery or server-side token/session rejection is established.

## Diagnosis and remaining unknowns

**UNDETERMINED.** The filtered logs do not contain a login entry for the controlled attempt, the controlled browser entry lacks a usable request correlation ID, and no matching login-history or session row was recorded in the queried window. This is insufficient to classify the issue as cookie-not-issued, cookie-blocked, cookie-not-sent, incomplete password change, or server token/session rejection.

Minimum additional evidence needed is the operator’s local existing Network entry for that exact attempt: its login status and `Set-Cookie` names/attributes, cookie exclusion reason if any, whether the expected cookie names were sent on its correlated `/me` request, and the sanitized `/me` error category. Do not share cookie values or credentials. No further login or authentication request is authorized by this phase.

## Preservation, tests, and prohibited actions

- `git diff --check`: passed.
- Approved frontend modifications remain untouched:
  - `static/js/dashboard.js`
  - `static/js/executive-dashboard.js`
  - `templates/dashboard/notifications.html`
  - `templates/layouts/base.html`
  - `templates/layouts/employee_base.html`
- Existing report hashes remained unchanged:
  - `qa-evidence/phase5d-performance-baseline.md`: `68B72CAEE9BE75A20E43EFD0E8DE3265BE677BCB0E8547205F70A568B6EE7937`
  - `qa-evidence/phase5h-performance-baseline-20261009-1158.md`: `6C131DA132E1709F431A1E1C9B0C8C6A6CD57856921920B09C00AA8C48CED42D`
  - `qa-evidence/phase5h-auth-verification-20261009.md`: `7C99851D3BF0B6E3C205F9888427B2A7D303FF9D4501A70B5D7F10FF4A426E97`
- `app/phase5c_diagnostics.py` remains absent.
- No tests were run. No application, configuration, migration, dependency, or deployment file was changed.
- This phase made no authentication requests, retries, password/recovery actions, cookie/session/account changes, performance measurements, or production accesses. The database inspection used a read-only transaction.
