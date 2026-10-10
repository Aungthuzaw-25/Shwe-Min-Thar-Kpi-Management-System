# Phase 5H-L — Controlled QA Authentication Verification Plan

Date: 2026-10-09

## Verdict

**BLOCKED — AUTHENTICATION UNVERIFIED**

This phase is preflight and planning only. No authentication request, browser interaction, account/session/database mutation, test, or performance measurement was performed.

## QA isolation evidence

Preflight checks passed:

- Repository: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- Commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- Docker context: `desktop-linux`
- `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa` are running and each has Compose project label `kpi_management_system_qa`.
- API identity: `ENV=qa`, host `postgres-qa`, port `5432`, database `kpi_qa`.
- PostgreSQL `current_database()`: `kpi_qa`.
- No health request was made in this phase; the prior QA gate recorded HTTP 200.
- No production URL or resource was used.

The existing QA auth/root-cause report was reviewed. It reports that the account was active, unlocked, and local-password-enabled at the time of a read-only snapshot; no active persisted sessions were observed then. It also records that the authorized attempt could not be correlated to login or `/me` logs. These observations do not establish a root cause.

## Recovery and test review

The documented `app/database/qa_recover.py` recovery procedure:

- Fails closed unless `ENV=qa`, the target is `postgres-qa:5432/kpi_qa`, and the exact account is `qa.superadmin@example.test` with expected role Super Admin.
- Requires an operator-entered new password twice and the explicit confirmation `RESET QA PASSWORD`.
- Replaces the password, records password history, sets `must_change_password=true`, and revokes active auth sessions and linked OAuth accounts.
- Was inspected only; it was not executed.

`tests/test_qa_recover.py` exercises target allowlisting, environment rejection, confirmation, password policy/history, and expected recovery effects using test fixtures. Tests were not run because this phase is plan-only.

`docs/qa-environment.md` says QA startup skips the normal seed and describes explicit guarded seeding. Source inspection shows QA seed uses `QA_SEED_PASSWORD` as the hash value only when creating a user; for an existing user it updates selected role/position/status flags but does not replace the password hash. Rerunning it is not an appropriate password-repair shortcut: it changes QA fixture/business state and does not prove or safely correct the existing password. The QA recovery path is the documented explicit password-replacement route, but requires separate authorization.

## Unresolved questions

1. Are the operator's intended credentials valid for the existing account?
2. Does the login response issue `access_token` and `refresh_token` cookies, with what safe attributes, and does the browser accept them?
3. Does the browser send the access cookie on the normal `/api/v1/auth/me` call?
4. If the cookie is sent, which JWT, blacklist, persisted-session, or account-validation branch accepts or rejects it?
5. Does the login response set `must_change_password`, and if so, does the UI route to the required password-change page?
6. What is the account/session state at the time of any future authorized verification?

## Proposed verification sequence (not executed)

1. **Preflight again immediately before any future request.** Verify the repository, Docker context, container project labels/states, API QA identity, DB identity, and local health endpoint; stop on any mismatch. Confirm the browser origin is exactly the local QA origin. Do not use production credentials or endpoints.
2. **Operator enters the approved QA account credentials directly into the local QA login page.** No credentials are copied into chat, scripts, terminal command lines, logs, or reports. Make one intentional attempt only. Record only status and sanitized error category. This distinguishes credential acceptance from rejection but can itself write login history, reset/increment failure counters, or lock the account according to existing service logic.
3. **Inspect that login response locally without copying values.** Record response status; whether `Set-Cookie` names `access_token` and `refresh_token` are present; safe attributes (`HttpOnly`, `Secure`, `SameSite`, `Path`, and persistence/expiry category); and any explicit browser block/exclusion reason. Never expose values.
4. **Observe the UI-selected next page.** Record only whether it remains at login, reaches `/auth/change-password`, or reaches the expected dashboard. Do not interpret an HTTP 200 login response alone as authenticated state.
5. **If the flow does not require a password change, perform one normal same-origin `/api/v1/auth/me` verification through the browser's intended mechanism.** Record only status, redacted identity confirmation, role, KPI level, permission count, and sanitized error category. Inspect whether the request included the expected cookie names only; never read values. Stop on failure; no retry or refresh.
6. **If password change is required, stop.** The current authorization must explicitly include the password-change operation before the operator performs it. If separately approved later, have the operator enter both passwords directly in the QA page, then verify the normal resulting login and `/me` flow once under a fresh approved gate.
7. **Classify only from direct evidence.** Missing cookie, explicit block reason, cookie names absent from request, correlated server rejection category, password-change redirect, or successful `/me` identity may support a specific diagnosis. Otherwise retain `UNDETERMINED`.
8. **Post-check and preserve.** Repeat QA isolation and repository preservation checks; record outcomes without secrets. Do not proceed to performance routes in the authentication phase.

## Authorization required for a future execution

No execution is authorized by this plan. Before a later phase:

- Obtain explicit operator approval for one QA login attempt using the named allowlisted QA account. The login request itself may update failed-login counts/history or create a persisted session and cookies on success. Stop after one attempt; no retry is implicit.
- Obtain explicit approval for one `/api/v1/auth/me` request after login, since it is a new authentication-dependent application request. It is read-only for account/business state but validates session and may update access-time metadata depending on implementation.
- If `must_change_password=true`, obtain separate explicit approval for password change before the operator submits it. It replaces password state, adds password history, and revokes authentication sessions. This is not safely undone by restoring a prior hash; any rollback requires a separately authorized, human-controlled credential recovery plan and reauthentication.
- Do not run seed/recovery, reset lockout, revoke/create sessions, or alter OAuth state without a distinct explicit authorization naming the exact operation and QA-only target.
- A later performance resumption needs separate authorization after normal authentication is verified with `/api/v1/auth/me` HTTP 200 and the expected identity.

## Stop conditions and rollback considerations

- Stop on any QA boundary mismatch; do not repair or restart the environment.
- Stop after the single authorized login attempt if it fails, if the expected cookie metadata cannot be observed safely, or if the browser remains unauthenticated. No refresh, credential guess, second attempt, or state cleanup.
- A failed login may increment failure counters and history; do not clear or reverse that state without explicit authorization.
- A successful login creates an auth session and browser cookies. Do not revoke or clear them as cleanup; any such action would require separate authorization.
- A password change is a persistent credential mutation and revokes sessions. It cannot be transparently rolled back; require a separate operator-controlled recovery plan.
- No performance tests, warmups, benchmarks, production access, or deployment are included.

## Preservation checks

- `git diff --check`: passed.
- The five approved frontend files remain the only tracked diff filenames:
  - `static/js/dashboard.js`
  - `static/js/executive-dashboard.js`
  - `templates/dashboard/notifications.html`
  - `templates/layouts/base.html`
  - `templates/layouts/employee_base.html`
- Prior report hashes match:
  - `qa-evidence/phase5d-performance-baseline.md`: `68B72CAEE9BE75A20E43EFD0E8DE3265BE677BCB0E8547205F70A568B6EE7937`
  - `qa-evidence/phase5h-performance-baseline-20261009-1158.md`: `6C131DA132E1709F431A1E1C9B0C8C6A6CD57856921920B09C00AA8C48CED42D`
  - `qa-evidence/phase5h-auth-verification-20261009.md`: `7C99851D3BF0B6E3C205F9888427B2A7D303FF9D4501A70B5D7F10FF4A426E97`
  - `qa-evidence/phase5h-auth-log-correlation-20261009.md`: `6BD375D7D10383344B1ABEBC95F1AF6F9FCEAD951ADA380D6E7846C46E8FCB49`
  - `qa-evidence/phase5h-auth-blocker-closure-plan-20261009.md`: `42EDEA0A787655A4769534B1A472325665D547B8A23F8F9047CE6BF74AFFD6B6`
  - `qa-evidence/phase5h-network-history-inspection-20261009.md`: `2A243A41FB3244AD3B7EEC063E4739DA9044C1A2CEBA51780E22777AB773B4AE`
  - `qa-evidence/phase5h-auth-root-cause-diagnosis-20261009.md`: `2B071B395573DA580A1F69EF10E4937A9D487C997367C55591BB496C2E10EE2C`
- `app/phase5c_diagnostics.py` remains absent.
- No prior report, application source, frontend modification, database state, cookie, or session was changed.
- No authentication request, recovery, seed, password mutation, test, performance measurement, production access, or deployment occurred in this phase.
