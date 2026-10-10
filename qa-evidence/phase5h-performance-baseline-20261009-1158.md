# Phase 5H QA Performance Baseline — Blocked

Capture date: 2026-10-09 (local time; exact capture time for the browser
request was not separately recorded)

## Verdict and scope

**BLOCKED — no fresh performance baseline or root-cause finding was produced.**
This is a QA-only, measurement-gated report. The QA safety gate passed, but the
existing browser session did not pass authentication verification. No password
attempt, credential recovery, session manipulation, or authentication bypass
was attempted. No target route was requested and no application code was
changed.

## QA safety gate

Verified before the attempted authenticated identity check:

| Check | Result |
|---|---|
| Repository root | `C:/Users/Aung Thu Zaw/Desktop/Project/Kpi_management_system` |
| Docker context | `desktop-linux` |
| Compose project | `kpi_management_system_qa` |
| `kpi_api_qa` | Running |
| `kpi_postgres_qa` | Running |
| `kpi_redis_qa` | Running |
| `kpi_minio_qa` | Running |
| `kpi_scheduler_qa` | Running |
| API identity | `qa postgres-qa 5432 kpi_qa` |
| PostgreSQL `current_database()` | `kpi_qa` |
| `GET /health` at `http://127.0.0.1:18000` | HTTP 200 |

The API identity was checked without printing credentials or the full database
URL.

## Authentication gate and measurement status

The one existing-session `GET /api/v1/auth/me` attempt returned **HTTP 401**.
The expected role and KPI level could not be verified. No further authentication
attempts were made.

Because authentication was not verified:

- No requests were made to `/superadmin/audit-logs`,
  `/api/v1/kpi/audit-logs?limit=150`, `/profile`, or `/kpi-periods`.
- No warm-up or measured cohort was collected.
- No client-duration, ASGI-duration, request-ID, worker, status-distribution,
  or route-level error statistics are available for Phase 5H.
- No before/after comparison or fix-effect estimate is available.
- No database performance or dependency diagnosis was performed.

This is an authentication prerequisite blocker, not evidence of a performance
regression or a diagnosis of the historical latency.

## Source-control state

Starting branch: `main`  
Starting commit: `0a32e2b3564f0e4d0898adbe9c82dbc5643cfc61`

The starting worktree contained the five approved Phase 3 modifications:

- `static/js/dashboard.js`
- `static/js/executive-dashboard.js`
- `templates/dashboard/notifications.html`
- `templates/layouts/base.html`
- `templates/layouts/employee_base.html`

It also contained the previously created
`qa-evidence/phase5d-performance-baseline.md`. That Phase 5F report was read
before this phase and was not modified. No application source, configuration,
test, migration, dependency, or deployment file was changed. No tests were run
because no code was changed and authenticated functional checks were blocked.

## Historical evidence — not Phase 5H measurements

The Phase 5D client values below are previously reported, recalculated from
retained browser timing entries; they are not fresh Phase 5H measurements:

| Route | Requests | Median | P95 | Maximum |
|---|---:|---:|---:|---:|
| `/superadmin/audit-logs` | 30 | 30.4 ms | 34.1 ms | 39.6 ms |
| `/api/v1/kpi/audit-logs?limit=150` | 30 | 30.8 ms | 55.6 ms | 57.1 ms |
| `/profile` | 30 | 31.6 ms | 35.8 ms | 39.1 ms |
| `/kpi-periods` | 30 | 30.7 ms | 50.8 ms | 64.9 ms |

These are client/browser timings, not ASGI durations. Phase 5C ASGI values are
also historical and are not a comparable Phase 5H baseline.

The historical `/profile` incident remains unresolved:

- Application time: 501.54 ms.
- Browser wall time: 586 ms.
- Request ID: `833ba83d-8883-4031-a723-9a3e48d6521f`.
- Worker PID: 11.
- Root cause: unknown.
- Reproduced during Phase 5H: not assessed; authentication prevented route
  measurements.

Phase 5D previously reported that this event did not recur then. That does not
establish that it is fixed.

## Diagnosis and release status

No new latency observation was obtained, so no bottleneck is established and no
optimization is supported by this phase. Static source inspection is not a
substitute for an authenticated baseline or request-correlated measurements.

**Release readiness:** not assessed by Phase 5H. This report does not authorize
production deployment.

## Safety and production impact

Only the verified local QA stack was accessed. No production URL, Railway
resource, production credential, or production database was accessed. No
deployment, restart, rebuild, data mutation, or destructive Git operation
occurred.

## Next step

Obtain authorized QA access through the normal approved process outside this
measurement phase. Then request a separately approved, bounded measurement run
that retains request-level evidence durably. Do not infer that the historical
`/profile` event is fixed or optimize without new evidence.
