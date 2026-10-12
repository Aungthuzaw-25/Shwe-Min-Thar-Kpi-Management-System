# Performance and UI Verification

Capture date: 2026-10-12 (Asia/Yangon).  
Branch: `perf/ui-polish`.  
Current populated verification: isolated disposable stack at
`http://127.0.0.1:13000`.

The populated follow-up below supersedes the earlier small-fixture performance
claims. The historical contrast-only run remains below for context.

## Populated follow-up: changes and results

The previously retained CSS-only contrast correction in
[`executive-dashboard.css`](../../static/css/executive-dashboard.css) remains
unchanged. The only new application optimization is Chart.js 4.5.1, pinned and
self-hosted as [`chart.umd.min.js`](../../static/vendor/chart.umd.min.js) with
its license alongside it. Shared layouts load it with `defer` only on the five
chart dashboards; process tracking also loads the same local bundle. The
unchanged third-party jsDelivr origin was removed from both Node and FastAPI
CSP directives. Google Fonts remains allowed because the layouts still use
it. No endpoint, response shape, authorization, business logic, database
schema, cache, or user data was changed.

### Evidence from matched cold/throttled captures

At 1.6 Mbps, 150 ms RTT, and 4x CPU, the old shared layout fetched Chart.js on
non-chart pages. After loading it only where charts are drawn:

| Role/page | Before requests / encoded bytes | After requests / encoded bytes | Difference |
|---|---:|---:|---:|
| L0 Tasks | 19 / 311,293 B | 18 / 240,136 B | -1 request, -71,157 B |
| L1 Tasks | 19 / 315,303 B | 18 / 242,714 B | -1 request, -72,589 B |
| L2 Tasks | 16 / 291,229 B | 15 / 218,689 B | -1 request, -72,540 B |

For chart dashboards, the CDN asset transferred about 72.5 KiB before and the
local bundle about 71.8 KiB after. A single sample showed the CDN request at
771–852 ms and local delivery at 1,229–1,276 ms; this does **not** prove a
page-speed improvement for those dashboards. The measured benefit is removing
the unnecessary ~71–73 KiB transfer from non-chart pages and eliminating the
mutable third-party script dependency.

With all external requests blocked, the original CDN-dependent dashboards had
no chart canvas; after the change, the local asset loaded (about 71.8 KiB) and
the dashboard canvas rendered. The verified blocked profile recorded no
successful external HTTPS request. On warm navigations both CDN/local assets
were cached, and the one-sample paint variations are not sufficient to claim a
warm-load speed gain.

Page/API timings, SQL counts, query plans, waterfall details, fixture sizes,
and measurement limitations are documented in
[`PERF_BASELINE_2.md`](./PERF_BASELINE_2.md). The populated dashboard summary
endpoint's five-call 3x p95 was 329 ms (slightly over the ~300 ms target), with
23 steady SQL statements per call. No further backend optimization was made
because the evidence does not justify a schema change or cache.

### Populated dashboard screenshots

Before and after screenshots were captured at all requested widths for all
five roles with the 3x fixture:

- Before: [`screenshots/populated-before/`](./screenshots/populated-before/)
- After: [`screenshots/populated-after/`](./screenshots/populated-after/)

The screenshots show populated KPI scorecards, charts, and department tables.
They are visual evidence only; responsive horizontal overflow and the full
business workflow suite were not re-run as part of this asset change.

### Validation

- `web`: `npm.cmd test` — **114 passed** across 2 test files.
- `python -m py_compile app/main.py tools/perf_seed.py` — passed after cleanup.
- Temporary timing instrumentation and profiling scripts were removed; only
  the guarded `tools/perf_seed.py` remains from the temporary profiling tools.
- The isolated disposable seed completed successfully at 1x and 3x. The
  existing QA database and QA seed were not modified.
- The disposable UI measurement authenticated with all five seeded roles.
- No full Python test suite or create/edit/delete workflow suite was run in
  this follow-up.

No cache or Alembic migration was added. No commit, merge, or push was made.

## Earlier small-fixture contrast-only run (historical)

The sections below document the earlier QA fixture and contrast-only change.
Their small-fixture page/API measurements are not representative of the
populated follow-up above.

### Change summary

The only application change in that earlier run was a CSS-only contrast
correction. Its paired measurements did not establish a speed gain.

### Paired dashboard page measurements

Five warm navigations per role were run in the same browser context and QA
session. The before pass used the stylesheet from the backup tag; the after
pass used the modified stylesheet. Main-data-visible waits for the dashboard's
existing `#dashboardUpdated` completion text. p95 is nearest-rank over five
observations and is therefore a small sample.

| Role | Route | TTFB p50/p95 before → after | FCP p50/p95 before → after | LCP p50/p95 before → after | Main data visible p50/p95 before → after | First-nav requests before → after | Encoded bytes before → after |
|---|---|---:|---:|---:|---:|---:|---:|
| Super Admin | `/superadmin/dashboard` | 60/139 → 75/139 ms | 308/684 → 352/408 ms | 308/684 → 352/408 ms | 606.7/1,182.2 → 572.2/640.8 ms | 16 → 16 | 115,173 → 115,185 B |
| Admin | `/admin/dashboard` | 95/116 → 83/202 ms | 352/832 → 328/624 ms | 380/832 → 328/624 ms | 599.2/1,102.6 → 498.5/910.4 ms | 16 → 16 | 115,168 → 115,180 B |
| L0 | `/dashboard` | 67/117 → 73/90 ms | 300/520 → 312/376 ms | 300/520 → 312/376 ms | 462.1/733.3 → 565.0/632.7 ms | 15 → 15 | 111,730 → 111,742 B |
| L1 | `/dashboard` | 96/202 → 91/92 ms | 348/532 → 336/404 ms | 348/532 → 336/404 ms | 524.2/827.2 → 519.3/614.0 ms | 15 → 15 | 111,731 → 111,743 B |
| L2 | `/dashboard` | 65/101 → 107/121 ms | 332/488 → 348/424 ms | 332/488 → 348/424 ms | 471.2/673.8 → 551.4/723.1 ms | 15 → 15 | 111,691 → 111,703 B |

Encoded bytes are the Resource Timing `encodedBodySize` sum for first
navigation only, not complete network transfer size. Browser cache and
cross-origin timing restrictions apply.

All five roles meet the 1.5 s warm-local main-data target before and after.
Variations across these small cohorts are not attributable to the CSS-only
change; browser/server cache and pool warming affect the paired order. No
performance gain is claimed.

This paired navigation cohort covers the five role dashboards, not every
non-dashboard page. The all-pages/all-roles navigation target remains
unverified.

## Dashboard API latency

Twenty-five authenticated, sequential `cache: no-store` browser requests per
endpoint were measured before and after. Durations include browser, proxy, and
body-read time; these are not isolated ASGI timings.

| Endpoint | Browser p50/p95 before → after | Target | Result |
|---|---:|---:|---|
| `GET /api/v1/notifications/unread-count` | 28.9/94.1 → 26.6/38.8 ms | p95 < 300 ms | Met |
| `GET /api/v1/notifications?limit=15` | 24.0/30.5 → 25.7/62.0 ms | p95 < 300 ms | Met |
| `GET /api/v1/dashboard/executive/overview` | 28.0/53.3 → 29.6/59.9 ms | p95 < 300 ms | Met |
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 64.0/128.7 → 63.7/160.4 ms | p95 < 300 ms | Met |

### Temporary ASGI and SQL query observations

| Endpoint | Requests | SQL queries/request | Server p50/p95 | SQL > 100 ms |
|---|---:|---:|---:|---:|
| `GET /api/v1/dashboard/executive/overview` | 5 | 10 | 27.9/280.4 ms | 0 |
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 5 | 23 | 83.6/223.7 ms | 0 |
| `GET /api/v1/notifications/unread-count` | 5 | 8 | 22.9/26.6 ms | 0 |
| `GET /api/v1/notifications?limit=15` | 5 | 8 | 23.5/25.1 ms | 0 |

Query counts were collected with a temporary SQLAlchemy event listener and
ASGI middleware in a short-lived process; both disappeared with that process.
No statement exceeded 100 ms, so there was no measured slow SQL statement to
run through `EXPLAIN ANALYZE`. The 23 summary queries need a populated fixture
before they can be evaluated for N+1 behavior. No Server-Timing header or
permanent instrumentation was retained.

## API waterfall and duplicate calls

On the captured dashboards, the overview and two notification requests appear
in the initial page-load sequence, with their exact start offsets varying by
role and run. The summary request follows the overview, because its
`period_type` is derived from the selected period returned by that overview. No
duplicate dashboard API calls were observed in the five role dashboard
captures.

This sequential dependency was preserved; no safe way to parallelize the
summary request was established without changing behavior. The
“no sequential-when-parallel” target is therefore met only for the dashboard
calls shown to be independent; the dependent summary call is intentionally
serial. Other pages' waterfalls remain unmeasured.

## Contrast, data parity, and responsive checks

Contrast uses WCAG relative luminance for normal-size text:

| Element | Before | After | Result |
|---|---:|---:|---|
| Default dashboard badge | 1.32:1 | 8.01:1 | From fail to pass (AA: 4.5:1) |
| Partial-status badge | 1.16:1 | 6.61:1 | From fail to pass (AA: 4.5:1) |
| Data-quality notice | 1.16:1 | 8.36:1 | From fail to pass (AA: 4.5:1) |

The full rendered dashboard text was normalized only for the changing
“Last updated” timestamp and SHA-256 compared before/after. All five role
dashboards matched exactly; only styling changed.

For each role, browser document width was checked against viewport width and
screenshots were captured at all three sizes:

| Viewport width | Dashboard screenshots | Horizontal overflow |
|---:|---:|---|
| 375 px | 5 roles | None |
| 768 px | 5 roles | None |
| 1440 px | 5 roles | None |

Before screenshots: [`screenshots/before/`](./screenshots/before/)  
After screenshots: [`screenshots/after/`](./screenshots/after/)

## Test results

- `python -m pytest -q`: **348 passed, 4 skipped**.
- `web`: `npm.cmd test`: **114 passed** across 2 files.
- `tests/test_ui_dashboard.py`: **4 passed** against QA before the paired
  screenshot run. The paired run also rendered each of the five role
  dashboards after the stylesheet change and verified data-text parity.
- No end-to-end browser tests for logout, password change, user CRUD, KPI
  upload, notifications actions, or permissions save were present in the
  inspected UI test module; those workflows were not exercised in this run.
- `git diff --check`: passed (line-ending warning only).

## Targets and outstanding limits

| Target | Result |
|---|---|
| Warm-local dashboard main data within ~1.5 s | Met for the five measured role dashboards before and after |
| Key dashboard API browser p95 < 300 ms | Met for four sampled endpoints |
| No duplicate dashboard API calls | Met for the five captured role dashboards |
| No sequential calls where safely parallel | No avoidable case proved; summary remains dependent on overview |
| Representative 50+ user / multi-period / thousands-of-rows fixture | Not met; QA database-target guard prevents a separate DB name |
| Every application page and each role's full navigation waterfall | Not measured |
| EXPLAIN ANALYZE | Not run; no SQL statement exceeded 100 ms |
| Before/after data comparison | Exact rendered-text parity verified for all five role dashboards |

No Alembic migration or cache was added or proposed. No decision is currently
needed on migrations or caches. A populated disposable QA database and a full
authenticated page/workflow matrix are required before making claims about
scaled performance or declaring all Phase 4 targets complete.

## Delayed-database auth follow-up

This later, separate follow-up used the 3x disposable fixture and an isolated
Toxiproxy latency of 50 ms in each direction. It did not connect to Railway or
use production credentials. The prior auth implementation was measured before
the code change, then the same endpoints were measured after it.

| Endpoint | SQL statements before → after | Five-sample server p50 before → after | Notes |
|---|---:|---:|---|
| `GET /api/v1/auth/me` | 13 → 7 | 2,053 → 1,508 ms | 15 after samples later gave 1,330 ms p50 |
| `GET /api/v1/dashboard/executive/overview` | 15 → 9 | 3,122 → 2,521 ms | 15 after samples later gave 1,674 ms p50 |
| `GET /api/v1/dashboard/summary?period_type=MONTHLY` | 23 → 17 | 3,245 → 4,862 ms | Five-sample p50 was noisy; 15 after samples gave 2,596 ms |
| `GET /api/v1/users/super-admins` | 13 → 7 | 2,014 → 1,407 ms | Five samples per side |

`get_current_user` now fetches the user, blacklist state, valid-session state,
role permissions, permission overrides, and user departments in one SQL
statement on the valid-user path. For an unresolved subject it performs a
second state check so the established blacklist → session → account failure
precedence remains unchanged. No caching, migration/index, permission change,
pool configuration change, or token/session semantic change was made.

Five before/after populated response hashes and shapes matched for `/auth/me`,
executive overview, monthly summary, and the Super Admin list; tested status
codes were all 200. The focused auth, JWT/session, and permission suites passed
**46 tests**. The complete integration/write workflow suite was not run.

On five cold Fast 4G page navigations for `/superadmin/super-admins`
(170 ms browser RTT, 9 Mbps down / 1.5 Mbps up, 1x CPU), median LCP changed
from 3,380 to 3,040 ms, FCP from 2,848 to 2,096 ms, and table-data-visible
from 4,806 to 3,724 ms. Median request count remained 15; transfer changed
from 140,366 to 142,585 bytes. One after sample had a 21.8 s outlier. This is
an improvement in the median, not the tail, and this single page is not a
five-navigation/all-roles matrix.

### Still outstanding

- The delayed DB profile reproduces multi-second API time only under a
  deliberately injected ~100 ms RTT. It does not establish that Railway's
  actual service/database region or public/private URL is the production
  cause. Use [`RAILWAY_CHECKLIST.md`](./RAILWAY_CHECKLIST.md) to verify that.
- The <=300 ms API p95 target at <=5 ms DB RTT has not been evaluated.
- The full per-role/per-page five-navigation matrix, every requested write
  transaction, and the Playwright write-workflow suite remain unmeasured.
- No additional broad dashboard redesign or changes to API fetch flows are
  included in this follow-up.
