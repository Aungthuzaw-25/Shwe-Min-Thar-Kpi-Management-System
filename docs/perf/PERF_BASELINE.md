# Performance Baseline

Capture date: 2026-10-12 (Asia/Yangon).
Branch: `perf/ui-polish`.
Environment: local QA stack at `http://127.0.0.1:18000`, Chromium, authenticated QA users.

## Scope and data limitations

This is a dashboard baseline from the currently running QA environment, not a
representative large-data performance campaign. The existing QA database
contains 6 users, 1 KPI period, 3 KPI assignments, 0 KPI records, 0 tasks, and
0 notifications. The role dashboards consequently display their existing
empty-state content.

The QA application configuration validates that its database is named
`kpi_qa`. A separate database name was rejected by that guard. No application
data, QA seed, or fixture was changed to bypass it; the empty scratch database
created while checking isolation was removed. All disposable containers,
volumes, and their network were removed. A realistic 50+ user / multi-period /
thousands-of-rows data set remains unavailable. Do not use this small-data
baseline to claim scaled-load performance.

## Browser method

- Logged in once per role and measured five warm navigations per role in the
  same headless Chromium context.
- Captured paired before/after measurements using the stylesheet from the
  backup-tag commit and the contrast-corrected stylesheet, respectively.
- Main data is considered visible when the existing `#dashboardUpdated`
  element changes from its initial text to `Last updated: ...` (or the
  existing error state), after the dashboard data-loading flow completes.
- TTFB is Navigation Timing `responseStart`; FCP is the browser paint entry;
  LCP is the buffered Largest Contentful Paint entry; main-visible is elapsed
  browser time when the existing dashboard panel became visible.
- Request count and encoded-resource bytes are Resource Timing entries for the
  first measured navigation. Encoded bytes are not a complete network
  transfer total: browser caching and cross-origin timing restrictions apply.
- Screenshots were captured before any application code changes at 375, 768,
  and 1440 CSS pixels wide. These represent the empty QA fixture.

## Dashboard navigation measurements

| Role | Route | TTFB p50 / p95 | FCP p50 / p95 | LCP p50 / p95 | Main data visible p50 / p95 | Requests | Encoded bytes |
|---|---|---:|---:|---:|---:|---:|---:|
| Super Admin | `/superadmin/dashboard` | 60 / 139 ms | 308 / 684 ms | 308 / 684 ms | 607 / 1,182 ms | 16 | 115,173 B |
| Admin | `/admin/dashboard` | 95 / 116 ms | 352 / 832 ms | 380 / 832 ms | 599 / 1,103 ms | 16 | 115,168 B |
| L0 | `/dashboard` | 67 / 117 ms | 300 / 520 ms | 300 / 520 ms | 462 / 733 ms | 15 | 111,730 B |
| L1 | `/dashboard` | 96 / 202 ms | 348 / 532 ms | 348 / 532 ms | 524 / 827 ms | 15 | 111,731 B |
| L2 | `/dashboard` | 65 / 101 ms | 332 / 488 ms | 332 / 488 ms | 471 / 674 ms | 15 | 111,691 B |

All five measured main-data p95 values meet the 1.5 s warm-local target. These
five-navigation cohorts cover the five role dashboards, not every non-dashboard
page in the application.

Screenshots: [`screenshots/before/`](./screenshots/before/)

## Initial visual contrast findings

The dashboard badge and data-quality notice use light-colored text on light
surfaces. WCAG relative-luminance contrast calculations for the rendered color
pairs are:

| Element | Before foreground / background | Before contrast | WCAG AA normal-text target |
|---|---|---:|---:|
| Dashboard badge | `#bfdbfe` / `#f5f7fa` | 1.32:1 | 4.5:1 |
| Partial-status badge | `#fde68a` / `#f5f7fa` | 1.16:1 | 4.5:1 |
| Data-quality notice | `#fde68a` / `#f5f7fa` | 1.16:1 | 4.5:1 |

The notice colors are set in the shared
[`executive-dashboard.css`](../../static/css/executive-dashboard.css) and
appear on Super Admin, Admin, L0, L1, and L2 dashboards. These contrast
failures are directly visible in the before screenshots and justify a
CSS-only color correction; no markup, selector, data, or JavaScript changes
are required.

## Warm dashboard API latency

Each endpoint was requested sequentially 25 times from an authenticated
Super Admin browser session with `cache: no-store`. Durations include browser
and same-origin proxy overhead through full response-body consumption. They
are not isolated ASGI timings or scaled-data results.

| Endpoint | Samples | p50 | p95 | Maximum | Statuses |
|---|---:|---:|---:|---:|---|
| `GET /api/v1/notifications/unread-count` | 25 | 28.9 ms | 94.1 ms | 195.5 ms | 200: 25 |
| `GET /api/v1/notifications?limit=15` | 25 | 24.0 ms | 30.5 ms | 65.6 ms | 200: 25 |
| `GET /api/v1/dashboard/executive/overview` | 25 | 28.0 ms | 53.3 ms | 58.0 ms | 200: 25 |
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 25 | 64.0 ms | 128.7 ms | 193.6 ms | 200: 25 |

The sampled API p95 values meet the 300 ms target in this small local fixture.
The dashboard summary result must not be extrapolated to data-rich periods.

## Temporary SQL query instrumentation

A temporary SQLAlchemy `before_cursor_execute` / `after_cursor_execute`
listener and ASGI timing middleware were attached in a short-lived API process.
Five authenticated, read-only requests per endpoint were measured. Instrumented
code was not added to the repository or retained in the QA API container.

| Endpoint | Requests | SQL queries per request | Server p50 / p95 | SQL statements over 100 ms |
|---|---:|---:|---:|---:|
| `GET /api/v1/dashboard/executive/overview` | 5 | 10 | 27.9 / 280.4 ms | 0 |
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 5 | 23 | 83.6 / 223.7 ms | 0 |
| `GET /api/v1/notifications/unread-count` | 5 | 8 | 22.9 / 26.6 ms | 0 |
| `GET /api/v1/notifications?limit=15` | 5 | 8 | 23.5 / 25.1 ms | 0 |

The first request to each endpoint includes cold-process/pool effects. No
individual SQL statement exceeded 100 ms in these cohorts, so no slow statement
was selected for `EXPLAIN ANALYZE`. The 23 summary statements warrant a
populated-data trace before classifying them as N+1 queries; this empty fixture
does not establish row-scaled behavior.

## Observed dashboard API waterfall

On the five role dashboards, notification list and unread-count requests start
alongside executive overview. The monthly dashboard summary starts only after
the overview response has been read and the selected period type is known:

| Role | Overview start / duration | Summary start / duration |
|---|---:|---:|
| Super Admin | 342 / 271 ms | 614 / 180 ms |
| Admin | 208 / 91 ms | 300 / 260 ms |
| L0 | 139 / 171 ms | 328 / 134 ms |
| L1 | 258 / 137 ms | 399 / 180 ms |
| L2 | 413 / 127 ms | 546 / 129 ms |

The overview determines the selected period and its `period_type`, which the
summary request consumes. The observed ordering is therefore not sufficient
evidence that these calls can safely be parallelized without changing behavior.
No duplicate dashboard API calls were observed in these captures.

## Ranked bottleneck candidates and plan

1. **Measure the overview-to-summary dependency on populated periods.** The
   waterfall shows a serial request after overview, but the period type is
   derived from the overview response. No code change is justified until
   populated-period behavior and an equivalent safe request strategy are
   measured.
2. **Assess stylesheet and third-party asset cost with a complete trace.** Both
   shared layouts reference multiple local CSS files, Google Fonts, and an
   unpinned Chart.js CDN script. Dashboard Resource Timing shows 16–17 requests,
   but this capture did not establish each asset's blocking contribution or
   a reliable total transfer size. Capture an origin-labelled waterfall and
   compare before changing asset loading or bundling.
3. **Run representative database profiling.** Query counts and server timings
   are now captured for four key read endpoints, but current tables contain no
   tasks or KPI records. Until a disposable populated QA database can be
   initialized without weakening the QA database-target guard, N+1 behavior,
   row-scaled slow SQL, query plans, index changes, and aggregate costs cannot
   be ranked from evidence.
4. **Review empty-state contrast and responsive layouts against populated
   dashboards.** Screenshots show the same dashboard content structure across
   roles. The fixture has no real KPI charts or populated tables, so redesign
   decisions about those elements are deferred rather than based on invented
   data.

### Approval-gated work

- Any Alembic migration or index change requires approval before implementation.
- Any cache for KPI aggregates requires explicit approval and a documented
  invalidation plan before implementation.
- No migration or aggregate cache is proposed by this baseline.

## Validation already run

- `tests/test_ui_dashboard.py`: 4 passed against the existing QA environment.
- `web`: `npm.cmd test` — 2 files and 114 tests passed.
- `python -m pytest -q`: 348 passed, 4 skipped.
- Paired dashboard text hashes were identical for all five roles.
- The paired responsive check reported no horizontal overflow at 375, 768, or
  1440 px.
