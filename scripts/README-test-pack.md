# Test-data pack + Load smoke test

Test-only helpers. They create test data and measure read-path latency.
They change **no production code** and must **never** run against
production.

> **⚠️ TEST-BRANCH-ONLY WARNING — READ FIRST**
> Run these scripts ONLY against a TEST database (Neon test branch or a
> local disposable database). The pack script refuses production-looking
> targets, but the refusal is a backstop, not a substitute for pointing
> it at the right database. Double-check `DATABASE_URL` before every
> real run. If you are unsure which branch your URL points at, stop
> and check first — a pack run on the wrong branch creates 80+ users
> and hundreds of rows there (removable with `--wipe`, but do not
> rely on that).

## Files

| File | What |
|---|---|
| `scripts/test_data_pack.py` | Creates the pack (departments, employees, periods, formulas + period links, records, tasks, targets, one departed user). |
| `scripts/load_smoke.py` | Concurrent read-path smoke test (login → dashboard → reports → users → departed download → periods). |

## Prerequisites

- Repo venv active, dependencies installed (`pip install -r requirements-dev.txt`).
- The application seed has run on the TEST database (roles must exist:
  Super Admin, Department User). The pack fails clearly if they do not.
- A Super Admin account exists on the TEST database (for the load test
  login and as the pack's `created_by`/`assigner` where applicable).

## 1. Test-data pack

### Preview first (touches nothing, needs no database)

```bash
python scripts/test_data_pack.py --target test --dry-run
```

Expected output: the plan — 4 departments, 12 positions, 80 users,
3 periods (current month OPEN, two past months FINALIZED), 12 formulas
linked current to all 3 periods, 240 records (over-achievement / low
mix), 150 tasks, 8 targets, and the departed user with its leaving date.

### Real run (TEST database only)

```bash
export TEST_DATA_PACK_TARGET=test
export DATABASE_URL='postgresql+asyncpg://user:pass@test.example/testdb'
export TEST_PACK_PASSWORD='a-test-only-password-you-choose'
python scripts/test_data_pack.py --target test --confirm --db-name testdb
```

What the guards require (all of them, or the script refuses):

- `--target test` on the command line.
- `TEST_DATA_PACK_TARGET=test` in the environment.
- `--confirm` on the command line.
- `--db-name` exactly equal to the database in `DATABASE_URL`.
- `ENV` must not be `production`.
- The host/database name must not contain `prod` / `production` as a
  whole token (`my-prod-db` refuses; `products` does not).
- `DATABASE_URL` must be an async URL (`postgresql+asyncpg://` or
  `sqlite+aiosqlite://`). For non-localhost Postgres, SSL is enabled
  unless `TEST_PACK_SSL=false`.

Passwords: `TEST_PACK_PASSWORD` when set, otherwise one is generated
per run and printed **once** to stdout. It is never written to a file.
All pack users share that password, with `must_change_password=False`,
so you can log in as one directly.

### What gets created

- Departments `TEST-PACK: Department A…D`, positions per department ×
  L0/L1/L2, users `testpack.user.001@…080@testpack.local` spread
  across departments and levels, with department memberships and a
  department head per department (first L0 user, never the departed one).
- Periods: current month (OPEN), previous month (FINALIZED),
  month-before (FINALIZED), all monthly.
- Formulas: one per department × level (ACTIVE), with 4 categories
  (3 TARGET, 1 DEDUCTION with a Late Arrival rule), rule rows, and a
  CURRENT link to every one of the 3 periods.
- Records: one per user per period (80 × 3 = 240). Scores are
  deterministic (seed 20261028): ~15% over-achievement (score capped,
  excess in `exceeded_categories`, HIGHER and LOWER directions),
  ~15% deduction/low scores, the rest spread across the buckets.
  Names/departments are snapshotted on each record; records are frozen.
- Tasks: 150, mixed statuses (Completed, Pending L1/L0, Approved,
  Draft, Rejected), due dates ±30 days, owned round-robin by pack users.
- Targets: 8 (company-wide + per-department, current/previous months,
  plus one ACHIEVED and one MISSED for variety).
- Departed user: the last pack user (`testpack.user.080@…`), transitioned
  last to mirror the app's offboard: status RESIGNED, leaving date
  inside the current calendar year, login off, soft-deleted, email
  renamed to `deleted_<ts>_<original>`, and the leaving date stamped
  onto its kept records and tasks. Its records and tasks are NOT deleted.

### Reruns, reset, wipe, password recovery

```bash
# Safe rerun: existing pack rows are skipped (matched by unique email /
# name / label / title). The departed user is matched even after its
# email was renamed to deleted_<ts>_<original>.
python scripts/test_data_pack.py --target test --confirm --db-name testdb

# Start clean, then seed: deletes pack rows first, then seeds.
python scripts/test_data_pack.py --target test --confirm --db-name testdb --reset

# Remove everything the pack created, then stop (same guards required).
python scripts/test_data_pack.py --target test --confirm --db-name testdb --wipe

# Only change pack users' passwords (same guards; password from
# TEST_PACK_PASSWORD, or generated + printed once).
python scripts/test_data_pack.py --target test --confirm --db-name testdb --reset-password
```

Wipe matches **only** pack-marked rows: users whose email contains
`testpack.local` (this also catches the renamed departed address),
and rows whose name/label/title starts with `TEST-PACK:`. It never
touches anything else. To verify a wipe: rerun `--dry-run` for the
plan, then check the counts the wipe printed, then confirm in the app
that no `TEST-PACK` rows remain.

If you lose the pack password: set `TEST_PACK_PASSWORD` to a new
test-only value and run `--reset-password` (same guards).

## 2. Load smoke test

```bash
export TEST_SMOKE_SA_EMAIL='sa@example.com'
export TEST_SMOKE_SA_PASSWORD='the-sa-password'
python scripts/load_smoke.py --base-url http://127.0.0.1:8001
python scripts/load_smoke.py --base-url http://127.0.0.1:8001 \
    --users 50 --rounds 2 --json-out /tmp/load-smoke.json
```

- `--base-url` is the API root (direct API port, e.g. 8001; the web
  proxy port serves pages, not `/api/v1/*`). For the QA stack, point
  it at that stack's API root instead. The script ignores proxy
  environment variables, so a local stack is never routed through
  some unrelated proxy.
- `--users` is concurrent virtual users (recommended band 20–50,
  accepted 1–100). `--rounds` repeats the whole chain per user
  (default 3). Each round logs in fresh, so login is measured too.
- Chain: login → SA dashboard summary → reports summary → users list
  → departed yearly company-wide download (previous year) → period list.
- Report: per-step attempts, successes, errors, HTTP statuses, p50/p95
  (over successes), and up to 3 error samples. Credentials are never
  printed. The script refuses to run without the SA env vars, and
  refuses base-URL hosts that look like production.
- **Pass bar: 0 errors on every step, and p95 ≤ 2000 ms per step**
  (adjust with `--p95-budget-ms`). A failing bar exits non-zero but
  still prints the full report. The 2000 ms bar is a starting point
  for a local stack, not a production SLO — if the QA stack is slower,
  raise the budget explicitly and say so in the report; do not silently
  lower the bar by editing the script's default.

## What to send back after a live run

1. The pack's full stdout (counts + any refusal/error lines).
2. The load smoke's full stdout (per-step table + verdict), or the
   `--json-out` file.
3. Any failure: the step name, the error sample lines, and the command
   you ran (with passwords redacted).

## Troubleshooting

| Symptom | Likely cause / fix |
|---|---|
| Pack refuses: `TEST_DATA_PACK_TARGET` | You did not export it. Set it to `test`. |
| Pack refuses: `--db-name` mismatch | Copy the database name out of `DATABASE_URL` exactly. |
| Pack refuses: token `prod` | Your URL looks production-like. Stop and confirm the branch. |
| Pack fails: role missing | Run the application seed on the TEST database first. |
| Load refuses: SA env vars | Export `TEST_SMOKE_SA_EMAIL` / `TEST_SMOKE_SA_PASSWORD`. |
| Load login 401s | The SA account/password are for a different database than `--base-url` serves. Check both point at the same TEST stack. |
| Load p95 over budget, 0 errors | Latency, not correctness. Report it with the numbers; raise `--p95-budget-ms` explicitly if the stack is known-slower. |
