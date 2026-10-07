# KPI Executive Intelligence

## Purpose and boundary

The executive dashboard answers what finalized KPI results show, how results
compare with prior comparable periods, and where recorded coverage or status
conditions need review. It is a read-only aggregation layer; it does not
calculate or persist employee KPI scores.

The existing KPI calculation contract remains the source of individual
`KPIRecord` percentages, weighted scores, and status buckets. Executive
aggregation consumes those persisted values and does not resolve formulas,
re-score categories, or reinterpret target formulas. See
[the KPI calculation contract](./kpi-calculation-contract.md).

## API and data sources

- `GET /api/v1/dashboard/executive/overview?period_id=<uuid>` returns period
  choices, selected-period summary, status distribution, department aggregates,
  trend, factual signals, and attention areas.
- `GET /api/v1/dashboard/executive/employees?period_id=<uuid>&offset=0&limit=25`
  lists evaluated employees alphabetically within the caller's authorized
  dashboard scope.
- `GET /api/v1/dashboard/executive/employees/{user_id}?period_id=<uuid>` returns
  a persisted employee result and its KPI history.
- Sources are `kpi_periods`, `kpi_records`, active `users`, active
  `user_departments`, `departments`, `positions`, `kpi_formulas`, and
  `kpi_history`.

The existing `KPIPeriod` model does not store a period type. The API reports
the type only when non-deleted persisted KPI records establish exactly one
unambiguous `KPIRecord.period_type` for that period. Trend comparisons use
records with that same type; the service does not infer a type from a label or
period duration.

## Metric definitions

- **Average percentage** is the SQL average of one latest non-deleted KPI record
  per active employee and selected period, using its persisted `percentage`.
  Results tied to deleted periods, deleted/inactive users, or deleted
  departments are excluded. Records are ranked by update time, creation time,
  then record ID; this also protects the aggregate from legacy duplicate rows.
- **Evaluated employee count** counts those scoped employees with an included
  result. **No-result employee count** is active employees in the same user
  scope minus evaluated employees. If no KPI period/result can be evaluated,
  values remain unavailable rather than becoming fabricated zero percentages.
- **Status distribution** uses the persisted `EXCELLENT`, `VERY_GOOD`, `GOOD`,
  `FAIR`, and `POOR` buckets. Each percentage is its count divided by evaluated
  employees only; when the evaluated count is zero, percentages are unavailable.
- **Department averages** are factual averages of persisted percentages, not
  rankings. Department status counts are employee counts. Below-FAIR and
  at/above-GOOD percentages use evaluated employees in that department as the
  denominator (`POOR` is below FAIR; `GOOD`, `VERY_GOOD`, and `EXCELLENT` are
  at/above GOOD). Department rows are ordered by name, not performance.
- **Trend** includes up to the 12 most recent finalized/locked periods at or
  before the selected period, using only the selected record period type.
  `delta` is current average minus the previous comparable period average.
  Direction uses exact, unrounded comparison (`UP`, `DOWN`, or `FLAT`); there
  is no repository-defined tolerance. Display values are rounded to two
  decimals only after aggregation. If either comparison average is absent,
  direction is `UNAVAILABLE`.
- **Signals and attention areas** state measurable changes, FAIR/POOR counts,
  or missing finalized results. They do not assign performance judgments or
  employee rankings. Severity remains informational because no authoritative
  concentration threshold is defined.

## Scope and period behavior

The endpoints use the same authenticated role/level-derived user scope as the
unified dashboard summary. Super Admin sees the organization; Admin and L0/L1
scope follows active department memberships; L2 and other department users
remain limited to their existing personal scope. Employee detail returns 404
when a result is absent or outside that scope. Department results are omitted
for personal L2 scope. No new permission code or scope query parameter is
introduced.

Soft-deleted periods cannot be selected. An omitted period selects the newest
open period, then newest in-progress period, then newest finalized/locked
period, and finally the newest remaining non-deleted period. A selected period
is reportable only when finalized or locked, consistent with the existing
dashboard's persisted-result contract. Invalid or deleted IDs return 404.

Period boundaries and status are read from the existing KPI period model.
Executive Intelligence does not perform date-boundary conversion or derive
periods; it uses the application's existing stored period values. The
application timezone remains `Asia/Yangon`, while PostgreSQL is configured for
UTC.

## Explicitly unavailable data

Finalized category-level results are not independently stored, so category
intelligence is marked unavailable. Target actuals likewise are not stored as
an authoritative executive aggregate; target and actual are not reconstructed
from tasks or scoring-rule inputs. Attendance intelligence is not part of this
read-only KPI endpoint. The current COUNT target calculation contract is not
changed.

## Empty and failure states

- No non-deleted periods: the API returns an empty period list and an
  unavailable summary; the dashboard displays “No KPI periods available.”
- Finalized period with no results: evaluated count is zero, average is
  unavailable, and status percentages are unavailable.
- Open, draft, review, or cancelled period: KPI results and trend are
  unavailable until the period is finalized or locked.
- No comparable prior finalized period: the trend has no delta/direction
  comparison.
- Category/target source insufficiency is reported as unavailable, never as
  zero achievement.
- The browser displays loading, empty, and human-readable error states without
  exposing server or database exception details.

All executive endpoints are GET-only. They do not write KPI records, targets,
assignments, tasks, reviews, history, or audit data.
