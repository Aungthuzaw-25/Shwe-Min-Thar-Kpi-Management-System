# Phase 5D QA Performance Baseline

## Evidence status

This report preserves Phase 5D evidence for review. It does not represent a new
performance campaign. No production or Railway resources were accessed.

Evidence categories used below:

- **Verified in this audit:** QA identity, source-control state, Phase 5C
  diagnostic absence, and the existence of retained Phase 5D browser timing
  entries and temporary tool output.
- **Previously reported and recalculated from retained browser timing
  entries:** Phase 5D client-duration aggregates shown in the results table.
- **Previously reported, not independently reproduced:** the Phase 5B
  `/profile` incident measurements and causal attribution.
- **Unavailable:** a durable, standalone copy of all original request-level
  observations and a Phase 5D ASGI timing cohort.

The detailed Phase 5D browser tool output was available during this audit at
`%LOCALAPPDATA%\Temp\1791517765855-copilot-tool-output-8b6182b48f6f4c8bb344d564db99502d.txt`.
It contains per-request records including phase, route, sequence index, HTTP
status, redirect flag, final path, request ID, client duration, and fetch error.
It is temporary tool output, not a durable source artifact; this report does
not claim to preserve that complete request-level dataset. The shared browser
also retained 35 Resource Timing entries per route (5 warm-ups and 30 measured
requests). Those entries expose duration, response timing, and status, but not
the request ID, redirect result, or fetch error.

Capture timestamp: unavailable in the retained request records. Phase 5D
results below are historical; they were not collected anew for this report.

## QA safety gate

Verified before accessing QA endpoints:

| Check | Result |
|---|---|
| Docker context | `desktop-linux` |
| Compose project | `kpi_management_system_qa` |
| `kpi_api_qa` | Running |
| `kpi_postgres_qa` | Running |
| `kpi_redis_qa` | Running |
| `kpi_minio_qa` | Running |
| `kpi_scheduler_qa` | Running |
| API environment/database target | `qa`, `postgres-qa:5432/kpi_qa` |
| PostgreSQL `current_database()` | `kpi_qa` |
| QA `GET /health` | HTTP 200 |

The API database check reported only host, port, database, and environment;
credentials and the full connection string were not printed.

## Source-control state

| Field | Value |
|---|---|
| Repository root | `C:/Users/Aung Thu Zaw/Desktop/Project/Kpi_management_system` |
| Branch | `main` |
| Commit | `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61` |
| `git diff --check` | Passed |

The initial worktree had only these previously approved modifications:

- `static/js/dashboard.js`
- `static/js/executive-dashboard.js`
- `templates/dashboard/notifications.html`
- `templates/layouts/base.html`
- `templates/layouts/employee_base.html`

They were preserved. No application or configuration files were changed to
produce this report.

## Phase 5D methodology and provenance

The original Phase 5D report states that the same authenticated browser session
was used for sequential GET requests with `credentials: "include"` and
`cache: "no-store"`. Each route had 5 warm-up requests, excluded from its
30-request measured cohort. Client time was measured with browser
`performance.now()` from before `fetch()` until after the response body was
consumed. These are browser fetch/resource durations, not full page-navigation
or ASGI durations.

The shared browser's retained Resource Timing data independently supports
35 observations per route, a split of 5 warm-ups and 30 measured requests,
HTTP 200 status for all measured entries, and the duration aggregates below.
The P95 method is nearest rank: sorted observation at rank
`ceil(0.95 * sample_count)`.

### Previously reported and recalculated client results

These are historical Phase 5D values, recalculated from retained browser timing
entries; they are **not newly collected measurements**.

| Route | Warm-ups | Measured requests | Status distribution | Min | Median | P95 | Max | Errors | Redirects | >1,000 ms |
|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|
| `/superadmin/audit-logs` | 5 | 30 | 200: 30 | 27.8 ms | 30.4 ms | 34.1 ms | 39.6 ms | 0 reported | 0 reported | 0 |
| `/api/v1/kpi/audit-logs?limit=150` | 5 | 30 | 200: 30 | 27.9 ms | 30.8 ms | 55.6 ms | 57.1 ms | 0 reported | 0 reported | 0 |
| `/profile` | 5 | 30 | 200: 30 | 30.5 ms | 31.6 ms | 35.8 ms | 39.1 ms | 0 reported | 0 reported | 0 |
| `/kpi-periods` | 5 | 30 | 200: 30 | 29.3 ms | 30.7 ms | 50.8 ms | 64.9 ms | 0 reported | 0 reported | 0 |

The status counts and durations were independently visible in browser Resource
Timing entries. Error and redirect counts come from the previously reported
request-level tool output; Resource Timing alone cannot independently establish
them.

The client investigation trigger is a fetch duration greater than 1,000 ms.
No measured observation exceeded it. No request ID was required for threshold
correlation because no such observation occurred. No ASGI durations or worker
IDs were collected as part of the Phase 5D cohort.

## ASGI evidence kept separate

Phase 5D collected no ASGI cohort. Its client measurements must not be compared
as if they were server durations. The following are previously reported Phase
5C ASGI measurements and are not Phase 5D observations:

| Route | Requests | ASGI median | ASGI P95 | ASGI max |
|---|---:|---:|---:|---:|
| `/superadmin/audit-logs` | 50 | 30.89 ms | 52.22 ms | 145.19 ms |
| `/api/v1/kpi/audit-logs?limit=150` | 50 | 30.98 ms | 41.80 ms | 71.58 ms |
| `/profile` | 50 | 31.64 ms | 49.59 ms | 68.54 ms |
| `/kpi-periods` | 50 | 38.07 ms | 52.99 ms | 77.39 ms |

The Phase 5C application investigation trigger was ASGI duration greater than
500 ms. It cannot be evaluated for the Phase 5D cohort because Phase 5D did not
collect ASGI durations.

## Historical `/profile` incident

The incident is retained as unresolved historical evidence:

- Application time: 501.54 ms.
- Browser wall time: 586 ms.
- Request ID: `833ba83d-8883-4031-a723-9a3e48d6521f`.
- Worker PID: 11.
- Root cause: unknown.
- Reproduced in Phase 5D: no.

These figures were reported from Phase 5B, not newly measured or independently
reproduced here. The current QA API logs did not contain the historical request
ID. The event is not claimed to be fixed.

## Diagnostic cleanup and final verification

Verified during this audit:

- `app/phase5c_diagnostics.py` is absent.
- `app/main.py` has no Phase 5C instrumentation diff.
- The runtime has the normal middleware stack; no
  `Phase5CDiagnosticsMiddleware` is installed.
- Current QA API logs contain no `phase5c=true` records.
- The five approved Phase 3 worktree changes remain the only modified files.
- `git diff --check` passed.

The QA identity and health checks were repeated after the read-only audit
checks and passed again.

## Limitations and release decision

The request-level tool output and browser timing entries are temporary, and no
complete request-level dataset was added to this durable report. In particular,
the full sequence of request IDs and per-request observations is not preserved
here. The aggregate values are supported by retained browser timing entries,
but those entries do not retain request IDs, redirect outcomes, or fetch
exceptions.

The historical `/profile` event remains unexplained. A successful QA baseline
does not establish its resolution and is not sufficient authorization for
production release.

**Release readiness:** QA isolation and baseline checks passed; performance
evidence has the durability limitation above; the historical incident remains
unresolved. This report does not authorize production deployment.
