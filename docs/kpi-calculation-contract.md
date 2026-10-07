# Current KPI calculation contract

This document records the behavior implemented by the repository. It is not a
statement that every behavior is the final business requirement.

## Formula resolution

For date-based calculation, the engine first resolves the KPI period containing
the target date using a start-inclusive, end-exclusive interval. More than one
matching period is rejected.

Within that period, a single active assignment for the user takes precedence.
The assigned formula must be active, not deleted, and effective for the
selected month. Multiple matching assignments are rejected. If no usable
assignment formula is found, fallback resolution matches department, position,
an active department membership, and the normalized membership level (`L0`,
`L1`, or `L2`). It excludes inactive/deleted formulas and formulas effective
after the selected month. The remaining candidates are ordered by latest
effective month and then highest version. Equal-priority ambiguity is rejected.

## Category weights and maximum

The scoring helpers interpret weights up to and including `1.0` as fractions;
values greater than `1.0` are interpreted as percentages and divided by 100.
Weights are applied independently and are not normalized to sum to one. The
formula maximum is the sum of each category's maximum score multiplied by its
interpreted weight. Formula creation schema validation currently restricts an
individual weight to `0..1`, although scoring helpers also support stored
percentage-style values.

## Deduction categories

Category, rule, and input names are matched after trimming, collapsing
whitespace, and lowercasing. Applicable deductions are grouped by normalized
rule name. Within a group, only the greatest applicable deduction is used;
different groups' deductions are added. The category score is the configured
maximum minus those deductions, floored at zero. A deduction category with no
rules receives its full maximum.

## Timeliness

The current engine counts tasks with `is_late = true` in the calculation
period, excluding deleted tasks. A task with a due date is bounded by its due
date; a task without one is bounded by its creation timestamp. The interval is
start-inclusive and end-exclusive. Tasks identified by a non-empty qualifying
rejection-review comment are excluded from the late-task count. The calculation
does not derive a penalty from grace periods or late-day counts.

## Target categories

The target is selected by normalized category name and target month. The lookup
admits department-specific targets and global targets, excludes deleted rows,
and prefers a department-specific target over a global target. The target
selector also excludes rows whose status is not `ACTIVE`.

For `COUNT`, the current implementation computes:

```text
ratio = target / actual
ratio = clamp(ratio, 0, 1)
category_score = ratio * max_score
weighted contribution = category_score * category weight
```

This is the current implementation, not necessarily the final business
requirement. Other metric types use `actual / target` before the same clamp.
A missing target, target value of zero or less, or missing category actual
produces zero. For a positive COUNT target and actual zero, the ratio is
currently set to one to avoid division by zero, producing the category maximum.

## Final score and status

Weighted category contributions are summed. Percentage is zero if the
weighted formula maximum is nonpositive; otherwise it is the weighted score
divided by that maximum, multiplied by 100, and clamped to `0..100`.

Status thresholds are:

| Percentage | Status |
|---:|---|
| 90 and above | `EXCELLENT` |
| 80 to below 90 | `VERY_GOOD` |
| 60 to below 80 | `GOOD` |
| 40 to below 60 | `FAIR` |
| below 40 | `POOR` |

## Explicit calculation authorization and persistence

`POST /api/v1/kpi/calculate/explicit` requires an authenticated user. Its route
role gate allows only `Admin` and `Super Admin`; it then enforces the
`kpi.calculate` permission (implicitly granted to Super Admin by the shared
permission evaluator). Department User is rejected by the role gate.

The route checks record existence, user scope, and explicit context. A missing
record returns 404; mismatched user, period, formula, or assignment context
returns 422. The calculation persists the score to `raw_score` and
`weighted_score`, along with percentage and status.

History is added only when persisted calculation values or status change.
Same-result repeats do not add history or change `updated_at`. Changed results
add one transition; a same-result repeat after that does not add another.
Calculation itself does not create an audit-log row.
