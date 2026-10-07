# Executive Intelligence Period Metadata Authorization Policy

## Status

Accepted

## Decision

Executive Intelligence reporting-period metadata is authorization-scoped.

An authenticated actor may see a reporting period in Executive Intelligence
only when the actor's authorized Executive Intelligence result scope contains
qualifying KPI result data for that period.

## Scope

The following period metadata fields are authorization-scoped:

- period ID
- period label
- period type
- start date
- end date
- status

A period containing only KPI data outside the actor's authorized Executive
Intelligence scope MUST NOT appear in that actor's period selector or
`overview.periods`. A period with no qualifying KPI result data inside that
scope MUST also be omitted.

For the existing level scopes:

- **L0:** A period may appear when the actor has an authorized result or an
  authorized same-department result in that period. A period with only
  cross-department results MUST NOT appear.
- **L1:** A period may appear when the actor has an authorized result or an
  authorized same-department result in that period. A period with only
  cross-department results MUST NOT appear.
- **L2:** A period may appear only when the actor has an authorized result of
  their own in that period. A colleague-only or cross-department-only period
  MUST NOT appear.

## Result Authorization

Period metadata visibility does not grant access to KPI result data.

Employee identity, KPI records, scores, percentages, KPI status, history,
action plans, and department-specific results remain protected by the existing
Executive Intelligence authorization rules.

The existing result scope remains:

- **L0:** Own KPI results and authorized same-department KPI results; no
  cross-department KPI results.
- **L1:** Own KPI results and authorized same-department KPI results; no
  cross-department KPI results.
- **L2:** Own KPI results only; no same-department colleague or
  cross-department KPI results.

If the project establishes a more specific authoritative result-scope
contract, preserve it. This period-metadata decision does not widen or weaken
result authorization.

## Cross-Department Rule

A period whose qualifying Executive Intelligence KPI data exists only in
another department MUST NOT appear in the actor's period metadata.

## L2 Rule

L2 users MUST NOT receive period metadata solely because a same-department
colleague has KPI results in that period if the L2 actor itself has no
authorized KPI result in that period.

## Empty Period Rule

A period with no KPI result data inside the actor's authorized Executive
Intelligence scope MUST NOT appear in that actor's Executive Intelligence
period list. Do not fabricate or infer a result to make a period visible.

## Security Rationale

Period metadata can reveal the existence, timing, status, or operational state
of reporting activity. Therefore Executive Intelligence period metadata
follows the same authorization boundary as the underlying KPI results.

## Implementation Requirement

The implementation MUST derive period visibility from the same effective
Executive Intelligence authorization scope used to determine accessible KPI
results. The server must determine authorized result scope, derive distinct
period IDs from qualifying results in that scope, and return metadata only for
those periods.

The required data flow is:

```text
Authenticated Actor
        ↓
Effective Executive Intelligence Scope
        ↓
Authorized KPI Results
        ↓
Distinct Reporting Period IDs
        ↓
Period Metadata
        ↓
overview.periods
        ↓
Frontend selector
```

The server response is authoritative. Frontend filtering alone is insufficient;
the frontend must render the period options returned in `overview.periods` and
must not implement authorization independently.

`period_type` must be derived only from qualifying in-scope KPI result data.
It MUST NOT be derived from an out-of-scope record. Period metadata objects
must contain exactly these fields:

- `id`
- `label`
- `period_type`
- `start_date`
- `end_date`
- `status`

Do not include employee information, KPI scores, KPI record IDs, department
names, or authorization details in period metadata.

Unknown or unauthorized period IDs must not disclose period or result data.
Unknown period IDs retain the existing safe response: HTTP 404 with
`KPI period not found.`

## Non-Granting Property

Seeing a period does not grant access to any employee or KPI result outside
the actor's authorized scope.

## Required Future Validation

The remediation and runtime-validation phase must verify:

- **L0:** Own and authorized same-department periods appear; cross-department-
  only periods do not.
- **L1:** Own and authorized same-department periods appear; cross-department-
  only periods do not.
- **L2:** Own-result periods appear; same-department colleague-only and
  cross-department-only periods do not.
- Every actor receives only the approved period metadata fields and no
  unauthorized period IDs.
- Selecting an unauthorized period cannot retrieve unauthorized KPI results.
- Direct unauthorized employee detail remains HTTP 404.
- Unknown periods remain HTTP 404 with `KPI period not found.`
- Periods with no qualifying in-scope result are omitted.

Do not create actor-owned finalized KPI data solely to satisfy these tests.
