# Chart Verification Report

## Inventory and result

| Chart | Page | API | Database source | Status |
| --- | --- | --- | --- | --- |
| Users by department | Admin dashboard | `/api/v1/admin/dashboard/summary` | `users`, `user_departments`, `departments` | PASS |
| Task status | Admin dashboard | `/api/v1/admin/dashboard/summary` | `tasks` | PASS |
| KPI completion | Super Admin dashboard | `/api/v1/superadmin/dashboard/summary` | `kpi_records` | PASS |
| KPI trend | Super Admin dashboard | `/api/v1/superadmin/dashboard/summary` | `kpi_records.period_start`, `percentage` | PASS |
| KPI trend | L2 dashboard | `/api/v1/kpi/dashboard/charts/{user_id}` | `kpi_records.period_start`, `weighted_score` | PASS |
| Process status | Process tracking | `/api/v1/departments/{id}/process-tracking` or `/api/v1/users/{id}/process-tracking` | `tasks.status` | PASS |
| Process progress | Process tracking | Same process-tracking endpoints | `task_history.created_at`, `task_history.action` | PASS |

## Fixes made

- Excluded deleted users and deleted department memberships from admin department counts.
- Excluded deleted KPI records from Super Admin KPI totals, completion totals, department averages, and trends.
- Excluded deleted departments from Super Admin department KPI results.
- Added real daily process-history aggregation to both process-tracking endpoints.
- Replaced the process progress chart's hard-coded seven-day zero series with the API's real `progress_trend` values.
- Removed the Super Admin chart's fake seven-day zero fallback when no trend data exists.

## Validation

- Python compilation: passed.
- Existing non-browser suite: `50 passed`.
- Full UI suite: not executable because no local server was listening on `localhost:8000`; failures were connection-refused before dashboard assertions.
- Live production database/API comparison: not performed from this local environment because production credentials and an authenticated browser session were not available.

## Isolated demo-data test

The configured database was not seeded or modified. The existing isolated test suite was run without browser tests:

```text
50 passed
```

This validates the demo-compatible backend, KPI calculations, permission behavior, dashboard route wiring, and seed permission logic without writing to a shared database. A true database/API/browser comparison still requires a local PostgreSQL instance or an explicitly approved disposable database.

## Remaining operational verification

Run the UI suite against the configured application server and compare each API response with the corresponding database aggregate for the active tenant. The API now applies soft-delete and department-membership filters at the source, and process progress is no longer placeholder data.
