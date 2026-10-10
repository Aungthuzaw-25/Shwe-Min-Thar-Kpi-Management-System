# Phase 5H-S8 — QA Login Disabled Submit Button: Read-Only Root-Cause Investigation

Investigation time: 2026-10-10 00:29:17 +06:30 (Asia/Yangon)

## Verdict

**INCONCLUSIVE — MULTIPLE PLAUSIBLE CAUSES**

The immediate reason the Phase 5H-S7 browser action timed out is established: Playwright resolved the submit button but found it disabled, and its click action waits for the target to become enabled. The login page source intentionally disables the button after its submit handler begins and keeps it disabled while awaiting the login fetch.

Why the button was still disabled several minutes after the earlier login request returned HTTP 401 is not established. The handler's `finally` block should re-enable it after the fetch settles; no S7 request was found in the bounded logs; and the shared page is now on a dashboard rather than the login form. The available evidence cannot distinguish a stale/incomplete client-side loading state from a changed browser-page/session state or another browser execution issue.

## Safety gate

- Repository root: `C:\Users\Aung Thu Zaw\Desktop\Project\Kpi_management_system`
- Branch: `main`
- HEAD: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`
- The requested diagnosis report path did not exist before this report was created.
- Docker context: `desktop-linux`
- `kpi_api_qa`, `kpi_postgres_qa`, `kpi_redis_qa`, `kpi_minio_qa`, and `kpi_scheduler_qa` were running under Compose project `kpi_management_system_qa`.
- Allowlisted QA API identity: `ENV=qa`, database host `postgres-qa`, port `5432`, database `kpi_qa`.
- No production resource was inspected or contacted.

## Initial worktree state

The initial `git status --short --branch --untracked-files=all` showed branch `main...origin/main` with these tracked modifications:

- `static/js/dashboard.js`
- `static/js/executive-dashboard.js`
- `templates/auth/change_password.html`
- `templates/dashboard/notifications.html`
- `templates/layouts/base.html`
- `templates/layouts/employee_base.html`
- `tests/test_csrf_middleware.py`

It also showed pre-existing untracked evidence and test files. These were preserved; the final status is recorded below and differs only by this new diagnosis report.

## Source inspection

### Login form and initial button state

- [templates/auth/login.html](../templates/auth/login.html), line 187 defines `form#loginForm`.
- Lines 198 and 211 define the required email and password inputs.
- Line 233 defines `button#loginSubmitBtn` as `type="submit"` with **no initial `disabled` attribute**.
- Lines 52–62 style the button, including `:disabled`; this CSS changes presentation only and does not disable the button.
- Lines 288–290 load `csp-event-bridge.js`, `csrf.js`, and `notifications.js` before the inline login-page script.
- Lines 382–384 may prepopulate the email from `localStorage`; this does not disable the submit button.

### Disable/re-enable behavior

- In [templates/auth/login.html](../templates/auth/login.html), line 418 registers the form's submit handler.
- Lines 427–428 replace the button label with “Signing in…” and set `submitBtn.disabled = true`.
- Lines 430–436 await a `fetch` to `/api/v1/auth/login`.
- Lines 465–471 handle a non-OK response or a caught error.
- Lines 472–474 run `finally`, restore the original label, and set `submitBtn.disabled = false`.

Therefore the source provides a direct mechanism for the observed disabled/loading state: it is the submit handler's in-flight state. If the fetch promise does not settle, the handler remains awaiting and `finally` has not run. However, the earlier S6 request is recorded as completed with HTTP 401, after which this `finally` block would ordinarily run. There is no direct browser evidence that it did or did not run.

### Validation, initialization, and other conditions

- The `required` email and password attributes (lines 198 and 211) invoke native form validation. Invalid or empty values can prevent the submit event from firing, but validation does not itself set this submit button's `disabled` property.
- The template IDs referenced by the login handler (`loginForm`, `email`, `password`, `remember-me`, and `loginSubmitBtn`) match the elements defined in the template.
- The inspected login form contains no CAPTCHA or other challenge control.
- No startup code was found that sets `loginSubmitBtn.disabled = true`; the submit handler is the source location that sets and clears this property.
- A script initialization failure before line 418 could leave the listener unregistered, but would not by itself explain a disabled button. The S7 “Signing in…” button snapshot is consistent with the handler having run at some time; it does not establish when or which request caused that state.
- [app/web/auth_web.py](../../app/web/auth_web.py), lines 9–15 serves this login template.
- [app/modules/auth/router.py](../../app/modules/auth/router.py), lines 168–170 defines the JSON login POST route.

## Browser state observed without interaction

- At this phase, the shared page was identified as `Executive KPI Dashboard` at `http://127.0.0.1:18000/superadmin/dashboard`.
- A passive, read-only DOM query confirmed the login form and its login inputs were absent from the current page. No credential values were read or recorded.
- Therefore the current browser state is **not** the login-form state from the S7 timeout, and the S7 button's current disabled state cannot be rechecked in place.
- The prior S7 browser-tool action output reported that `button[type=submit]` resolved disabled and showed the “Signing in…” label; it timed out waiting for the button to become enabled. That is the direct evidence for its state at S7.
- The browser integration does not expose retained Network history. No Network entries were cleared or changed. The current browser observation does not establish how or when the page reached the dashboard.
- An earlier browser-tool output contained a CSP `style-src` inline-style violation. That message concerned inline styling, was not shown to block scripts, and is not evidence that it caused the disabled state.

## Existing QA evidence correlation

Read-only review of the existing Phase 5H-S6 and S7 reports established:

- S6 recorded a QA log entry at `2026-10-09T17:52:38.474Z`: `POST /api/v1/auth/login`, HTTP 401, request ID `3ae657f8-d339-4875-b55a-7618bce1d892`, server time `15892.95ms`.
- S6 browser action timed out while the page showed “Signing in…”. The response status is from the QA API log; the browser integration did not expose the native Network entry.
- S7's single later click action resolved to a disabled submit button, timed out, and its bounded QA log window contained no matching login, `/api/v1/auth/me`, or password-change entry.
- The S6 401 is historical and must not be attributed to S7. No successful S7 submission is confirmed.
- No new API request or authentication action was made during S8.

## Verified facts versus hypotheses

**Verified**

- The login button is enabled by default in the template.
- The login submit handler sets it disabled while awaiting the authentication fetch and restores it in `finally`.
- The S7 browser tool reported the button disabled and timed out waiting to click it.
- The S6 QA API log recorded a completed 401 login response.
- The S7 bounded log inspection found no corresponding authentication request.
- The current shared page is the dashboard, with no login form present.

**Hypotheses not confirmed**

- The S7 button may have reflected a stale or incomplete in-flight state left in the prior page.
- The page or browser context may have changed independently between S7 and this inspection.
- Another browser-side execution problem may have prevented the handler's `finally` state from being reflected.

No evidence distinguishes these possibilities. In particular, the S6 401 record is insufficient to prove the browser executed the corresponding `finally` block, and the current dashboard URL is not correlated to a verified authenticated identity.

## Safest next action

Do not interact with the current dashboard or attempt another login as part of this diagnosis. If further diagnosis is authorized separately, first decide how to inspect a fresh, controlled login-page state and its browser console/Network evidence without exposing credentials. Preserve current browser state and do not retry until separately authorized.

## Final integrity and restrictions

- No login or authentication request was made in this phase.
- No credentials, browser storage, cookies, CSRF state, or session state were read or changed.
- No database operation, container restart, source/configuration/test edit, deployment, or production access occurred.
- The existing Phase 5H reports and protected application files were not modified.
- `app/phase5c_diagnostics.py` remains absent; no Phase 5C instrumentation was introduced.
- No files were staged, committed, reset, stashed, or discarded.
- `git diff --check` and post-write hash checks are reported in the session response.
