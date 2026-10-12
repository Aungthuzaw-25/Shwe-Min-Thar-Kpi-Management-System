# Populated Performance Baseline (Follow-up)

Capture date: 2026-10-12 (Asia/Yangon).  
Branch: `perf/ui-polish`.  
UI/API host: isolated local disposable stack (`127.0.0.1:13000` / `127.0.0.1:18400`).  
Browser: Chromium driven by Playwright with Chrome DevTools Protocol (CDP).

This report supersedes the small-fixture limits in
[`PERF_BASELINE.md`](./PERF_BASELINE.md) for the measurements listed here. It
does **not** claim complete five-navigation coverage: the browser matrix below
has one timed navigation for each sidebar page exposed to each seeded role.
The API endpoint timing cohort separately has five sequential samples per
endpoint and fixture size.

## Disposable fixture

The existing QA database, seed, and guard were not changed. A separate
PostgreSQL container, volume, network, API, web process, and proxy used a
database named `kpi_qa` so the unchanged guard could pass. The seed script
refuses non-local, non-marked databases.

| Fixture | Total users (generated) | Departments | Positions | KPI periods | Formulas | Assignments / records | Tasks | Notifications |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| 1x | 60 (54) | 6 | 18 | 14 | 21 | 840 / 840 | 5,000 | 3,000 |
| 3x | 180 (174) | 6 | 18 | 14 | 21 | 2,520 / 2,520 | 15,000 | 9,000 |

The 14 periods comprise 12 monthly periods plus one quarterly and one yearly
period. The fixture is deterministic. Each generated run replaced only rows
owned by the performance seed.

## Browser method and coverage

- **Current / warm:** localhost, same authenticated browser context per role;
  every discovered target route was visited once to warm it before its timed
  sidebar navigation. Service workers were blocked to prevent a pre-existing
  cache from changing the result.
- **Cold / throttled:** new context, CDP cache disabled, 150 ms RTT, 1.6 Mbps
  download and upload, and 4x CPU throttling.
- **Cold / throttled / third parties blocked:** same as cold/throttled, with
  Google Fonts and jsDelivr blocked using both context routing and CDP blocked
  URL rules. The corrected capture confirms zero successful external HTTPS
  requests.
- CDP `Network` events recorded request origin, resource type, start offset,
  duration, encoded transfer bytes, status, failure, and a parser-based
  render-blocking estimate. API request paths, duplicates, and non-overlapping
  sequential pairs are in the raw captures linked below.
- Page FCP/LCP use the browser paint entries. The script's generic
  “main-data-visible” text heuristic did not identify a reliable completion
  marker on every page (notably departments and charts under profile C); those
  values are intentionally excluded from the ranking.

The pages found in each role's sidebar were:

| Role | Timed sidebar pages |
|---|---:|
| Super Admin | 11 |
| Admin | 9 |
| L0 | 3 |
| L1 | 3 |
| L2 | 4 |

Targets absent from a role's sidebar were recorded as unavailable and were not
accessed by bypassing the UI. This includes pages such as profile/tasks for
roles whose layouts do not expose those sidebar links. The L0/L1 dashboard
sidebar aliases also did not match the shared `/dashboard` route in the
measurement harness; those dashboards were nevertheless opened for the
populated responsive screenshots. Thus this is not an every-role/every-route
authorization matrix.

## Page measurements

The following are medians over the one successful observation per
page/role/profile where available. `n` is the number of role observations
contributing to that page. FCP/LCP are milliseconds. Cold transfer is the
median total encoded bytes for profile B; its variation includes API payload
differences and browser timing noise.

| Page | n | A: warm FCP/LCP | B: cold FCP/LCP | C: external blocked FCP/LCP | B: requests / KiB |
|---|---:|---:|---:|---:|---:|
| Dashboard | 2 | 396 / 1,872 | 1,764 / 2,392 | 1,284 / 2,692 | 19 / 221 |
| Tasks | 3 | 420 / 1,076 | 2,524 / 2,956 | 1,740 / 2,732 | 19 / 304 |
| Super Admin KPI | 2 | 320 / 816 | 1,476 / 1,984 | 992 / 2,356 | 20 / 289 |
| Users | 2 | 328 / 1,052 | 1,568 / 1,996 | 1,036 / 1,936 | 18 / 245 |
| Departments | 2 | 344 / 656 | 1,392 / 1,892 | 844 / 1,520 | 16 / 238 |
| Permissions | 2 | 504 / 944 | 1,804 / 2,328 | 888 / 2,064 | 20 / 253 |
| Positions | 2 | 368 / 368 | 1,732 / 1,732 | 1,168 / 1,168 | 16 / 211 |
| KPI periods | 2 | 476 / 476 | 1,448 / 2,176 | 1,160 / 1,556 | 17 / 220 |
| System settings | 2 | 492 / 1,500 | 2,320 / 3,816 | 1,340 / 2,956 | 19 / 247 |
| Profile | 3 | 348 / 348 | 1,376 / 1,820 | 956 / 956 | 14 / 190 |
| Notifications | 5 | 392 / 716 | 1,196 / 1,988 | 1,000 / 1,372 | 16 / 190 |
| Target | 2 | 296 / 296 | 1,296 / 2,020 | 1,020 / 1,020 | 17 / 215 |
| Super Admin management | 1 | 456 / 676 | 1,292 / 1,876 | 796 / 796 | 16 / 207 |

Profiles B/C are individual observations per role/page, not five-observation
cohorts. Their timing differences are directional only. The cold profiles do
show the full multi-CSS/font/script request waterfall and are useful for
locating payloads; they do not provide a sufficiently repeated page-latency
comparison.

Raw browser captures:

- [Warm baseline](./perf_baseline_2_data.json)
- [Cold/throttled baseline](./perf_baseline_2_throttled_before.json)
- [Corrected third-party-blocked baseline](./perf_baseline_2_thirdparty_blocked_before_verified.json)
- [Warm after](./perf_baseline_2_warm_after.json)
- [Cold/throttled after](./perf_baseline_2_throttled_after.json)
- [Corrected third-party-blocked after verification](./perf_baseline_2_thirdparty_blocked_after_verified.json)

## Backend endpoint timings and query counts

Temporary ASGI timing and SQLAlchemy cursor instrumentation was used only on
the disposable API. Each endpoint was called sequentially five times from an
authenticated Super Admin session at each fixture size. Server time is the
API application's `Server-Timing` duration; it excludes browser/network time.
The query listener counted SQL statements and logged statement text (without
bound parameter values) when cursor execution exceeded 100 ms. `p95` is the
nearest-rank maximum of five observations, so the first-request warm-up effect
is visible. SQL-count columns list the steady final-three counts; the full
five-count sequences are in the raw JSON files.

| Endpoint | 1x server p50/p95 ms | 1x steady queries | 3x server p50/p95 ms | 3x steady queries |
|---|---:|---:|---:|---:|
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 134 / 510 | 23 | 176 / 329 | 23 |
| `GET /api/v1/dashboard/executive/overview` | 78 / 128 | 15 | 85 / 97 | 15 |
| `GET /api/v1/dashboard/executive/employees` | 29 / 56 | 9 | 43 / 49 | 12 |
| `GET /api/v1/tasks` | 62 / 93 | 12 | 58 / 72 | 12 |
| `GET /api/v1/tasks/approval-queue` | 20 / 20 | 8 | 22 / 37 | 8 |
| `GET /api/v1/tasks/categories/available` | 30 / 52 | 11 | 35 / 40 | 11 |
| `GET /api/v1/departments/` | 17 / 20 | 8 | 18 / 39 | 8 |
| `GET /api/v1/kpi/periods/` | 36 / 52 | 8 | 25 / 37 | 8 |
| `GET /api/v1/kpi/formulas` | 38 / 61 | 9 | 39 / 44 | 9 |
| `GET /api/v1/permissions/` | 17 / 18 | 8 | 22 / 24 | 8 |
| `GET /api/v1/permissions/role/{role_id}` | 21 / 27 | 8 | 21 / 38 | 8 |
| `GET /api/v1/permissions/user/{user_id}/effective` | 31 / 35 | 15 | 36 / 38 | 15 |
| `GET /api/v1/permissions/users` | 31 / 34 | 12 | 59 / 184 | 12 |
| `GET /api/v1/notifications` | 19 / 20 | 8 | 24 / 27 | 8 |
| `GET /api/v1/notifications/unread-count` | 18 / 19 | 8 | 20 / 26 | 8 |
| `GET /api/v1/system/settings` | 18 / 19 | 8 | 20 / 22 | 8 |
| `GET /api/v1/system/settings/email/providers` | 18 / 20 | 8 | 19 / 21 | 8 |
| `GET /api/v1/targets/` | 31 / 42 | 8 | 26 / 30 | 8 |
| `GET /api/v1/users/roles/list` | 29 / 31 | 9 | 28 / 38 | 9 |

The summary endpoint's counts were `73, 37, 23, 23, 23` at 1x and
`58, 33, 23, 23, 23` at 3x. The final three samples remained at 23 statements
at both sizes, so this trace does not show a query-count-per-row increase.
However, the 3x endpoint p95 is 329 ms for the five-request cohort, slightly
above the ~300 ms target; steady final-three p50/p95 are 162/176 ms. Treat the
warm-up and steady-state figures separately.

The `/api/v1/users/` and `/api/v1/users/super-admins` probes returned HTTP 500:
the existing QA Super Admin address `qa.superadmin@example` fails the
application's `UserResponse` email validation because it lacks a dotted
domain. This is existing QA fixture data; the QA seed and validation were not
changed. The `executive/employees` benchmark used the observed
`period_id`, `offset`, and `limit` query parameters after an initial probe
without those parameters returned 422.

Raw endpoint samples:

- [1x Server-Timing and SQL counts](./perf_backend_timing_1x.json)
- [3x Server-Timing and SQL counts](./perf_backend_timing.json)

## Slow SQL and query plans

At 3x, the dashboard summary first-load cohort logged three SQL statements at
or above 100 ms: a KPI-record monthly aggregate (135.11 ms), a distinct KPI
period query (100.10 ms), and a department KPI comparison (125.63 ms). The
largest statement applies `row_number()` over KPI records, joins KPI periods,
filters finalized/locked records, and aggregates the selected monthly records.

Read-only `EXPLAIN (ANALYZE, BUFFERS)` reproductions against the 3x fixture:

- Monthly overall KPI aggregate: **13.854 ms**, 4,405 shared-buffer hits,
  2,160 monthly records passed through the window operation, and 2,160 primary
  key user lookups.
- Department KPI aggregate: **8.242 ms**, 116 shared-buffer hits, seven
  departments and 2,160 monthly rows in the aggregate.

Those warm PostgreSQL plans are much faster than the initial instrumented
cursor observations; first-call connection/statement and process warm-up
effects are material. Both plans used a sequential scan of the 2,160-row
monthly `kpi_records` subset. No index migration is proposed: the measured
fixture is not large enough to prove a new index is beneficial, and any
migration requires approval.

## Waterfalls and ranked bottlenecks

1. **Dashboard summary is the backend endpoint closest to the p95 target.**
   At 3x, all five calls used 23 SQL statements after two warm-up calls, and
   the five-call p95 was 329 ms. The query plan over 2,160 monthly KPI rows
   completed in 13.854 ms when warm; do not attribute the whole application
   duration to that single statement.
2. **Repeated API calls remain visible on page load.** Notifications and
   unread-count requests appeared twice on Notifications pages; the KPI
   management page repeated a departments request. The Users and Permissions
   pages also showed sequential pairs (roles → departments → users, and
   role permissions → effective user permissions). The timing harness records
   the start/duration pairs in the raw captures. No request-flow change was
   made because safe parallelization was not established.
3. **Chart.js was an unnecessary cold transfer on non-chart pages.** In
   profile B, task-page captures transferred about 71–73 KiB less and issued
   one fewer request after Chart.js was restricted to chart pages. This is the
   directly measured, retained page-load improvement.
4. **Cross-origin fonts remain in the layouts.** Blocking Google Fonts
   changes the third-party profile; self-hosting Inter was not attempted.
5. **Large script parse time, event-loop blocking, and row-scaled N+1 behavior
   remain unranked.** The measured query counts were stable in the 1x/3x
   endpoint cohort, but this does not prove absence of N+1 behavior at larger
   production volumes.

No backend optimization, cache, or migration was applied. Backend fixes beyond
the safe Chart.js asset change remain gated on more representative repeated
page captures and approval for migrations/caches.
