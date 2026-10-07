# QA KPI Historical Data and Fixture Contract

This document records the approved disposition of the historical QA KPI
records below and defines the minimum provenance contract for future controlled
QA fixtures. It does not define a production data-retention or migration policy.

## Legacy historical QA data

**Disposition: LEGACY HISTORICAL QA DATA — NON-AUTHORITATIVE FOR CANONICAL
CALENDAR SEMANTICS**

Preserve these rows as-is. Their original provenance and calculation inputs
have not been established. They are not canonical fixtures and must not be
used to demonstrate that historical periods followed the current
Asia/Yangon calendar contract.

| Period ID | Label | Observed status |
| --- | --- | --- |
| `992b3479-56fe-485c-82e9-9ac8f2d2aad0` | QA Phase 3.4.7 KPI Contract Probe May 2026 | FINALIZED |
| `4f71e418-2ec7-4712-8580-6778c9da85f8` | QA Phase 3.4.7 Protected-End Representation Probe June 2026 | FINALIZED |
| `251cb178-9e06-41ff-8497-c8a16c9a9560` | QA Phase 3.4.6 Boundary Contract Probe | OPEN |
| `3f794888-7a63-4c6f-a0af-5bc1af3b72e0` | QA September 2026 | OPEN |
| `4c1d1630-c709-469d-a279-471a1dc84159` | QA Phase 3.4.5 Disposable October Ranking Probe | OPEN |

Known associated active KPI records:

| Period | KPI record ID |
| --- | --- |
| May | `c1c94360-d7f6-4324-b078-d75601405fb6` |
| June | `296c147d-8812-44e7-8459-53e869393487` |
| August | `d95154dd-e608-4c1a-ab65-6d0223c61c6d` |
| September | `22a7b8c0-ce3d-45dd-b40e-3675cfaa48b7` |
| September | `c423f59e-0348-4f78-ab97-7a82b2108d62` |
| October | `c0402031-5dc6-4bb3-afb6-71b780320503` |

For this legacy set:

- Do not delete, soft-delete, update, canonicalize, recalculate, or unlock
  these periods or records.
- Do not change their assignments, tasks, reviews, evidence, formulas,
  history, scores, percentages, or statuses.
- Do not use their current score, percentage, status, timestamps, history, or
  matching task dates as proof of the original evaluation window or score
  inputs.
- No KPI calculation-window or calculation-input snapshot is persisted on
  these records. Tasks are not linked to these records as authoritative
  historical inputs.
- History entries do not establish the task/review/target inputs used for the
  original calculation.
- The repository's September QA seed definition is technically plausible
  context only; it does not prove that the seed created the existing database
  row or establish the row's calculation lineage.
- The stored boundaries remain unchanged, including the shifted Yangon
  presentation. Do not interpret those old boundaries as canonical or unlock
  the finalized May and June periods to make them appear canonical.

Keep these IDs out of canonical-calendar regression assertions and controlled
fixture manifests. They may be inspected as legacy data only, with the above
limitations stated.

## Canonical calendar contract for future QA

Future controlled period fixtures use `Asia/Yangon` local calendar boundaries
and the half-open range `[start_inclusive, end_exclusive)`. Date-only periods
start at local midnight and end at the following period's local midnight.
Full-containment queries use:

```text
record_start >= requested_start
AND record_end <= requested_end
```

A record ending exactly at the requested exclusive boundary is included; a
record crossing that boundary is excluded. OPEN records are excluded from
evaluated dashboard aggregates; FINALIZED and LOCKED records are included
where the existing query supports them. Charts retain rolling `days_limit`
semantics. Monthly ranking uses the current-month start; other ranking
behavior remains as currently implemented. Weekly and monthly scheduler
rollups use the calendar helper and `Asia/Yangon` timezone.

## Explicit calculation context

The explicit KPI calculation API accepts a `record_id` and may additionally
receive `assignment_id`, `period_id`, `formula_id`, and `user_id` for
cross-checking. The record's linked user, formula, and period are authoritative.
The explicit path rejects missing, deleted, frozen, overridden, inactive, or
contradictory context and does not fall back to global date-based period lookup.
It updates only the validated record; it does not create a replacement record.

Existing date-based bulk, custom, upload, and scheduler callers retain their
global date-to-period resolution behavior. Task/review input collection remains
date- and owner-based because tasks do not have period, assignment, or record
foreign keys. Explicit record context therefore does not establish authoritative
historical task attribution. Do not use the explicit endpoint on the protected
legacy records listed above.

The existing naive-datetime-as-UTC policy remains in force. Fixture timestamps
should use explicit timezone offsets, preferably explicit `Asia/Yangon`
boundaries, rather than relying on naive timestamp interpretation.

Canonical examples below are for future controlled test data only; they are
not instructions to edit the legacy rows:

| Month | Start inclusive (`Asia/Yangon`) | End exclusive (`Asia/Yangon`) |
| --- | --- | --- |
| May 2026 | `2026-05-01 00:00` | `2026-06-01 00:00` |
| June 2026 | `2026-06-01 00:00` | `2026-07-01 00:00` |
| August 2026 | `2026-08-01 00:00` | `2026-09-01 00:00` |
| September 2026 | `2026-09-01 00:00` | `2026-10-01 00:00` |
| October 2026 | `2026-10-01 00:00` | `2026-11-01 00:00` |

## Future controlled fixture manifest

Every fixture that creates or tests persisted KPI data must declare its
provenance and expected behavior in a test-data manifest or equivalent
version-controlled fixture definition. This is a contract, not a claim that
any new fixtures were created in this phase. All values below are required
when applicable; a field that is not applicable must be explicitly marked
`not_applicable` with a short reason, not silently omitted.

| Manifest field | Required declaration |
| --- | --- |
| Fixture identity | Stable, unique fixture ID; purpose; owning test/module; environment (`qa` only); declared creation timestamp with timezone |
| Period | Period type; timezone; start; end; explicit `end_exclusive: true`; expected calendar-helper result; expected lifecycle status |
| Organization and user | Exact department, user, role/level, and position fixture identities; no implicit reuse of unrelated live QA users |
| Formula | Exact formula ID or fixture identity, version, effective period, categories, weights, evaluation types, scoring rules, and maximum score |
| Assignment | Exact KPI assignment identity and its period, user, department, position, and formula links |
| KPI record | Exact expected record identity or documented deterministic ID rule; period and assignment links; expected `raw_score`, `weighted_score`, `percentage`, and status |
| Tasks | Exact task fixture IDs, owners, categories, due dates, creation timestamps when relevant, statuses/submission state, lateness fields, and the reason each task is in or out of the calculation window |
| Reviews | Exact review IDs, task links, reviewer identity and level, action, and exact deduction comments/values consumed by scoring |
| Evidence | Exact evidence IDs and task links, or explicit `not_applicable`; evidence must not be assumed to affect scoring without an asserted rule |
| Targets | Exact target fixture IDs, target month, department scope, category/metric type, and target values used by TARGET categories |
| Manual inputs | Exact declared manual input mapping passed to calculation, or an explicit empty mapping |
| Expected score inputs | Exact task, review, target, and manual-input identities contributing to each category; expected deduction items and intermediate category values |
| Expected history | Expected history rows and old/new values, or an explicit assertion that no history row should be created |
| Dashboard | Expected inclusion or exclusion and the query/range/status condition being tested |
| Ranking | Expected inclusion or exclusion, ranking period, and relevant status/range conditions |
| Scheduler | Expected behavior for the fixture (normally scheduler not invoked by unit tests); if tested, declare exact QA-only job and expected result |
| Isolation and cleanup | QA target guard, exact created-row ID allowlist, transaction or rollback strategy, idempotency behavior, and exact cleanup scope |

Fixture principles:

1. Use stable, explicit inputs and calendar boundaries; avoid current-date
   dependence and hidden date arithmetic.
2. Isolate fixture-owned users, departments, formulas, assignments, tasks,
   reviews, evidence, targets, and records. Do not depend on whichever rows
   happen to exist in the QA database.
3. Make the expected scoring result independently assertable from the declared
   formula and input rows. Record both raw and weighted values even when the
   current engine stores equivalent values.
4. Use fixed fixture IDs where the test framework supports them, or document
   and capture deterministic generated IDs. Idempotent setup must not silently
   adopt or mutate a row outside the fixture's explicit ownership.
5. Require an explicit QA environment and database-target guard before any
   persisted fixture setup. Never point fixture setup or cleanup at production.
6. Clean up only exact IDs created by that fixture, within its transaction or
   declared rollback scope. Never clean by month, label, or broad QA-table
   predicate.
7. Assert period boundaries, lifecycle status, calculation inputs, expected
   scores, and dashboard/ranking behavior; do not infer correctness from a
   successful insert or current score alone.
8. Keep fixture provenance in version-controlled test/QA material. Do not add
   production schema fields or runtime behavior solely to label test rows.

## Existing boundary test coverage

`tests/test_kpi_calendar_ranges.py` already covers the daily, weekly, monthly,
quarterly, and yearly calendar ranges; exclusive next-boundary behavior;
full-containment filtering including an end equal to the requested exclusive
boundary; and exclusion of a record crossing the requested boundary. Continue
to use this read-only/unit-test coverage rather than modifying historical QA
rows to exercise calendar behavior.
