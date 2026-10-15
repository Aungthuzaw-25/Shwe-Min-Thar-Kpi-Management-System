# Phase 5H-S6 — Controlled QA Authentication Runtime Evidence

## Verdict

**BLOCKED — LOGIN FAILED**

The single authorized browser action was associated with one QA API log entry for `POST /api/v1/auth/login`, which completed with HTTP 401. No `/api/v1/auth/me` entry was present in the bounded log window. This supports a rejected login response, but does not establish why it was rejected. No authenticated identity was verified.

## Authorization and scope

The operator explicitly authorized exactly one controlled authentication attempt using the existing QA login tab and the intended QA Super Admin account. The operator had previously confirmed native DevTools Network preparation.

No retry, password reset, account unlock, database change/query, credential recovery, source or configuration change, deployment, production access, or performance test occurred. No passwords, cookies, tokens, authorization headers, or sensitive response bodies are included here.

## QA isolation gate

Immediately before browser interaction:

- Repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- HEAD: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa` were running under Compose project `kpi_management_system_qa`.
- Allowlisted API identity: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- The target report path did not exist before the attempt.
- No production resource was inspected or contacted.

## Sanitized attempt timeline

All times are Asia/Yangon (+06:30); UTC equivalents are supplied for log comparison.

| Time | Evidence |
|---|---|
| 2026-10-10 00:22:12.252 (+06:30) / 2026-10-09 17:52:12.252Z | Attempt start marker, recorded immediately before invoking the single sign-in action. |
| Immediately after the marker | One browser-tool click action was invoked on the existing Sign in control. The tool returned a 10-second locator timeout rather than a successful action result. Its attached page snapshot showed the sign-in button disabled with “Signing in…”; this UI state alone is not treated as proof of a request. No second action was attempted. |
| 2026-10-10 00:22:38.474 (+06:30) / 2026-10-09 17:52:38.474Z | One matching QA API access-log record: `POST /api/v1/auth/login`, HTTP 401, request ID `3ae657f8-d339-4875-b55a-7618bce1d892`, server processing time `15892.95ms`. This request record is the evidence that a login request reached the QA API. |
| Through 2026-10-10 00:22:56.541 (+06:30) / 2026-10-09 17:52:56.541Z | Bounded QA API log inspection found no `/api/v1/auth/me` record and no additional matching authentication request. Evidence collection stopped; no retry was made. |

## Evidence and interpretation

- **Login POST:** Captured in QA API access logs at `/api/v1/auth/login`; HTTP 401; request ID and processing time are recorded above.
- **Browser Network panel:** The integrated browser tooling did not expose native DevTools Network entries to this agent. Consequently, browser-observed response headers, including `X-Request-ID`, and any response body were not independently inspected. The request ID above is from the QA API access log.
- **`/api/v1/auth/me`:** No matching request was found in the bounded QA API log window. Whether the browser issued an unlogged or otherwise unavailable `/me` request is not established; no `/me` status or identity response was captured.
- **Outcome:** The login endpoint returned HTTP 401, so this attempt is classified as a rejected login. The cause is undetermined. This is not evidence of successful login with failed session validation, and it does not establish an authenticated identity.
- No request or response secrets, cookie values, tokens, authorization values, or sensitive response bodies were read into this report.

## Preservation and final state

- The requested report path was confirmed absent before writing; this report is newly created and does not overwrite prior evidence.
- Existing Phase 5H reports, protected frontend files, and pre-existing tests were not modified.
- No source, template, test, configuration, or logging changes were made.
- `app/phase5c_diagnostics.py` remains absent; no Phase 5C instrumentation was introduced.
- No files were staged or committed, and pre-existing worktree changes were preserved.
