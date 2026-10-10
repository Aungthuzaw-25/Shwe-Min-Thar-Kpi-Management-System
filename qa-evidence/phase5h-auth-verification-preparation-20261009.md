# Phase 5H-Q — CSRF Fix Review and Observable Auth Verification Preparation

Review time: 2026-10-09 23:32:22 +06:30 (time supplied for this phase)

## Verdict

**PREPARATION COMPLETE — RUNTIME AUTHENTICATION STILL UNVERIFIED**

Authentication status remains **BLOCKED — AUTHENTICATION UNVERIFIED**. This phase did not attempt to authenticate or contact the running application.

## QA safety gate

Verified before reviewing the change:

- Working directory and repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- The five expected containers (`kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, `kpi_scheduler_qa`) were running and labeled with Compose project `kpi_management_system_qa`.
- Allowlisted non-sensitive API identity fields: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- No HTTP request or database connection was made. No production resource was accessed.
- The requested report path did not exist before creation.

## Phase 5H-P CSRF change review

Sources reviewed:

- `templates/auth/change_password.html`
- `static/js/csrf.js`
- `app/main.py::CSRFMiddleware`
- `tests/test_csrf_middleware.py`
- `tests/test_password_change_csrf.py`
- `qa-evidence/phase5h-password-change-csrf-fix-20261009.md`
- `app/middleware/logging_middleware.py`

### Findings

1. **The shared helper already injects the expected header.** `static/js/csrf.js` reads `csrf_token` from `document.cookie` and sets `X-CSRF-Token` on same-origin non-safe `fetch` calls when a token exists. The password-change page loads this helper before its inline handler.
2. **The explicit header does not conflict with the helper.** The Phase 5H-P inline handler reads that same cookie and supplies the same header. The wrapper then reads the cookie again and sets the same header value. The helper does not append a second value; it calls `Headers.set`. With no intervening asynchronous work, both reads refer to the same cookie state.
3. **Cookie parsing matches this application's token format.** The middleware creates `csrf_token` using `secrets.token_urlsafe(32)`, whose URL-safe alphabet does not require percent-decoding. Both the inline code and helper split `document.cookie` on `"; "` and select the exact `csrf_token=` prefix. This follows the browser's serialized cookie format and does not trim the token value; the generated token contains no whitespace. The implementations would be brittle for a future token format containing spaces or requiring URI decoding, but that does not apply to the current generated token.
4. **The server compares the submitted values.** For a non-safe request authenticated by an `access_token` cookie and without a bearer header, `CSRFMiddleware` reads the `csrf_token` cookie and `x-csrf-token` header. It uses `secrets.compare_digest` to require a non-empty exact match and returns 403 otherwise. The password-change route is not exempt.
5. **Test evidence has two different strengths.** The middleware tests exercise the middleware directly at `/api/v1/auth/change-password`: matching values continue to the test handler, while missing/mismatched values return 403. The template test is static-text inspection; it asserts source strings for cookie parsing, header, missing-token message, endpoint, credentials, body and redirect. It does not execute JavaScript or inspect an actual constructed browser request.
6. **The inspected tests are deterministic and isolated.** The middleware tests use synthetic Request objects and a simple in-memory response handler; the template test reads a repository file. None requires browser interaction, service calls, QA data, or credentials. This phase did not execute any tests, as required.

The Phase 5H-P explicit header is redundant with the shared helper but consistent with it and provides clear local intent plus a fail-closed missing-cookie message. No correction is proposed from this review. Remaining uncertainty is browser/runtime behavior: these tests do not demonstrate what headers a real browser sends or what the running QA deployment serves.

## Future operator evidence checklist

This checklist is for a **future, separately authorized** verification only. It does not authorize a login attempt. Credentials must be entered by the operator directly in the local QA browser, never shared in chat or copied into commands or reports. A normal password-change flow also requires explicit authorization if needed.

### Before the single authorized attempt

1. Reconfirm the browser is at the approved local QA origin `http://127.0.0.1:18000`; do not use a production URL.
2. Open DevTools **Network** before interacting with the login page. Enable **Preserve log** so the login row remains available across the subsequent document navigation. Clear old rows if appropriate so the one authorized flow can be distinguished.
3. Do not export a HAR, copy raw headers, inspect/copy request bodies, or use console scripts to issue requests. Network entries may contain credentials in the login request body, password data in a change-password request, and token values in the login response.
4. Record the local clock/timezone to use for sanitized timestamps. The API's `X-Request-ID` response header is the preferred correlation key to the existing request logger.

### Observe the one operator-controlled flow

5. The operator enters credentials in the browser and submits once. Do not retry. On the `POST /api/v1/auth/login` Network row, record the request time and HTTP status. Record the response `X-Request-ID` if present. Never inspect or copy the request body or token-bearing JSON response body.
6. Before navigating away from that response row, inspect only the `Set-Cookie` metadata for `access_token` and `refresh_token`: whether each name was issued and safe attributes (`HttpOnly`, `Secure`, `SameSite`, `Path`, expiry category). Do not copy cookie values. In DevTools' Cookies/Issues view, record whether each cookie was accepted or the browser's stated exclusion reason. Record `csrf_token` only as the CSRF cookie name and safe attributes; never its value.
7. Record the visible route reached after login: `/auth/change-password` or an authenticated dashboard route (including which dashboard path, without copying profile data). Do not infer the route from response JSON. The browser's destination indicates which frontend branch ran, but does not by itself prove `/me` authentication.
8. If a naturally occurring `GET /api/v1/auth/me` appears, record its time, HTTP status, response `X-Request-ID`, and sanitized result only (for example, successful response versus error category). In its request headers, confirm only whether `access_token` appears among cookie names; never reveal or copy values. Do not manually call `/me` if the application does not naturally request it. If absent, record the gap.
9. If an authorized normal password-change flow is required, inspect its naturally generated `POST /api/v1/auth/change-password` row before its subsequent redirect. Record status, `X-Request-ID`, presence of the `csrf_token` cookie name, and presence (not value) of the `X-CSRF-Token` header. Record whether the server accepts it (success status) or rejects it (sanitized error category). Never inspect/copy the body or header/cookie values.
10. After successful password change, record the visible redirect to the login route and any approved `next` destination category. The application intentionally revokes sessions on password change, so successful completion redirects to login to create a fresh session. Do not treat password change alone as proof of a fresh authenticated session.

### Correlate with existing QA logs and preserve evidence

11. For each relevant request, correlate its response `X-Request-ID` with the existing `api.access` log line. `RequestLoggingMiddleware` logs request ID, method, path, status and process duration; it does not log request/response bodies or cookie/token headers. It also logs the request ID back in `X-Request-ID`. Match IDs first; use timestamps (including timezone) only as a secondary check. Do not enable new logging or add instrumentation.
12. In the evidence note, retain only method/path, status, sanitized timestamp, request ID, safe cookie attributes/names, cookie acceptance/exclusion category, header/cookie **presence**, redirect route, and sanitized error category. Redact client IP if it appears in a copied log line. Do not paste raw headers, HAR files, bodies, email/name/profile data, credentials, CSRF token values, access/refresh tokens, or authorization values.
13. Capture the login response/cookie evidence before its page navigation, the `/me` row before leaving the Network panel, and the password-change request/response before its own redirect. Preserve log is useful for retaining rows, but capture those sanitized facts while the relevant row and browser cookie exclusion details remain accessible.
14. Stop after that single authorized flow. If it fails, evidence is incomplete, a second attempt appears necessary, or an unexpected sensitive prompt occurs, stop and obtain separate operator authorization rather than retrying or using recovery.

No new instrumentation, middleware, or logging is needed for this collection procedure.

## Preservation and prohibited actions

Pre-review fingerprints were recorded for all pre-existing modified tracked files and untracked reports/tests. Post-report verification confirmed their hashes and status were unchanged. The only new file is this report. `git diff --check` passed. The five previously approved frontend files remain unmodified by this phase. `app/phase5c_diagnostics.py` remains absent; no Phase 5C instrumentation was added. No files were staged or committed.

No HTTP/authentication request, browser interaction, database connection, test execution, account/session/database mutation, credential reset, seed/recovery/migration/bootstrap command, production access, deployment, or performance measurement occurred.

## Final status

**PREPARATION COMPLETE — RUNTIME AUTHENTICATION STILL UNVERIFIED**

The review establishes source behavior and a safe evidence collection procedure only. It does not prove whether the prior login succeeded, whether a password-change flow ran, or whether authentication currently works in QA. Keep the blocker open pending a separately authorized and reliably observed operator flow.
