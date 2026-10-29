"""Formula Builder period resolution (Database design, Stage 2).

One place owns every period reference a formula save can carry:

- existing period ids (UUIDs),
- ``YYYY-MM`` month references (find-or-create per month),
- a ``{start_month, end_month}`` custom range (inclusive expansion).

Rules implemented here, all without any schema change:

- Month matching uses the **Yangon** calendar month of a period's
  ``start_date`` (a Yangon Oct 1 00:00 start is UTC Sep 30 17:30,
  so a UTC month would misfile it as September). Candidates are
  non-deleted, non-CANCELLED; an exact-month period wins a tie,
  otherwise the earliest created wins. No global one-per-month
  constraint exists (quarterly/yearly periods legitimately share a
  start month); concurrent creators serialize on a Postgres
  advisory lock per month key.
- Auto-created periods are DRAFT, labelled ``"{Month} {Year}"``,
  spanning the Yangon month via the existing
  ``calendar_period_range(MONTHLY)`` helper.
- Unlinking a period that already has scores for the formula is
  blocked (whole request, nothing changes) with the period label
  and record count in the message.
- Overlap warnings reuse the engine's own audience matcher
  (department + position fallback + level + region); they never
  block a save.
"""

import calendar
import re
from datetime import date
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.kpi.models import (
    FormulaStatus,
    KPIFormula,
    KPIFormulaPeriod,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.kpi.models import PeriodType

MONTH_REF_RE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")
MAX_CUSTOM_RANGE_MONTHS = 60


def parse_month_ref(value) -> tuple[int, int] | None:
    """Strict ``YYYY-MM`` -> (year, month), else None."""
    if not isinstance(value, str):
        return None
    match = MONTH_REF_RE.match(value.strip())
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def month_label(year: int, month: int) -> str:
    return f"{calendar.month_name[month]} {year}"


def month_bounds(year: int, month: int):
    """(start_inclusive, end_exclusive) datetimes for a Yangon month."""
    period_range = calendar_period_range(PeriodType.MONTHLY, date(year, month, 1))
    return period_range.start_inclusive, period_range.end_exclusive


def _iter_months(start: tuple[int, int], end: tuple[int, int]):
    year, month = start
    while (year, month) <= end:
        yield year, month
        month += 1
        if month == 13:
            year, month = year + 1, 1


def expand_month_range(start_month: str, end_month: str) -> list[tuple[int, int]]:
    """Inclusive month list for a custom range, with validation."""
    start = parse_month_ref(start_month)
    end = parse_month_ref(end_month)
    if start is None or end is None:
        raise HTTPException(
            status_code=400,
            detail="Custom period range needs YYYY-MM start and end months.",
        )
    if start > end:
        raise HTTPException(
            status_code=400,
            detail="Custom period range start month must not be after its end month.",
        )
    months = list(_iter_months(start, end))
    if len(months) > MAX_CUSTOM_RANGE_MONTHS:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Custom period range must not exceed {MAX_CUSTOM_RANGE_MONTHS} "
                f"months (got {len(months)})."
            ),
        )
    return months


async def _take_month_lock(db: AsyncSession, year: int, month: int) -> None:
    """Serialize concurrent creators of one month (Postgres only;
    SQLite test runs are single-threaded and skip the lock)."""
    if db.get_bind().dialect.name != "postgresql":
        return
    await db.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
        {"key": f"kpi_period_month:{year:04d}-{month:02d}"},
    )


async def find_period_for_month(
    db: AsyncSession, year: int, month: int
) -> KPIPeriod | None:
    """The period for a Yangon month: exact-month match wins a tie,
    otherwise the earliest created candidate wins.

    Second step (paired with the create-time overlap block):
    a period COVERING the month's first day — e.g. a Sep 15–
    Nov 15 span covering October, which starts in no month it
    covers. Without this step, find_or_create would insert a
    duplicate October that overlaps the span (the endpoint
    block cannot cover auto-create, which bypasses the
    endpoint). Reusing the covering period is correct: the
    engine already resolves the month's dates to it, so a
    duplicate would break calculation, and failing the save
    would break a formula whose month IS covered. Create only
    when neither step matches.
    """
    month_start, next_month_start = month_bounds(year, month)
    candidates = (
        await db.execute(
            select(KPIPeriod).where(
                KPIPeriod.is_deleted.is_(False),
                KPIPeriod.status != KPIPeriodStatus.CANCELLED,
                KPIPeriod.start_date >= month_start,
                KPIPeriod.start_date < next_month_start,
            )
        )
    ).scalars().all()
    if not candidates:
        candidates = (
            await db.execute(
                select(KPIPeriod).where(
                    KPIPeriod.is_deleted.is_(False),
                    KPIPeriod.status != KPIPeriodStatus.CANCELLED,
                    KPIPeriod.start_date <= month_start,
                    KPIPeriod.end_date > month_start,
                )
            )
        ).scalars().all()
    if not candidates:
        return None

    def _as_instant(value):
        # PG returns aware instants (comparison is exact); SQLite
        # drops tzinfo on storage, and what it stores is Yangon
        # wall-clock, so a naive value is read as Yangon time.
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=APPLICATION_TIMEZONE)
        return value

    def sort_key(period: KPIPeriod):
        exact = (
            _as_instant(period.start_date) == month_start
            and _as_instant(period.end_date) == next_month_start
        )
        return (0 if exact else 1, period.created_at, str(period.id))

    return sorted(candidates, key=sort_key)[0]


async def find_or_create_period_for_month(
    db: AsyncSession, year: int, month: int, *, allow_create: bool
) -> KPIPeriod | None:
    """Find the month's period, or auto-create it (DRAFT) when the
    caller may create periods. Returns None when the month has no
    period and creation is not allowed."""
    await _take_month_lock(db, year, month)
    period = await find_period_for_month(db, year, month)
    if period is not None:
        return period
    if not allow_create:
        return None
    month_start, next_month_start = month_bounds(year, month)
    period = KPIPeriod(
        label=month_label(year, month),
        start_date=month_start,
        end_date=next_month_start,
        status=KPIPeriodStatus.DRAFT,
        is_deleted=False,
    )
    db.add(period)
    await db.flush()
    return period


async def resolve_period_refs(
    db: AsyncSession,
    refs,
    *,
    can_create_periods: bool,
) -> list[UUID]:
    """Resolve save-time period references to existing period ids.

    Each ref is a period UUID (existing, non-deleted, else 422) or a
    ``YYYY-MM`` month (find-or-create; a missing month without the
    ``kpi.create`` permission is a 422 naming the month, never a
    silent permission escalation). Anything else is a 422.
    Order-preserving, deduplicated.
    """
    resolved: list[UUID] = []
    seen: set[UUID] = set()
    unknown_ids: list[str] = []
    missing_months: list[str] = []

    async def _append(period_id: UUID) -> None:
        if period_id not in seen:
            seen.add(period_id)
            resolved.append(period_id)

    for ref in refs or []:
        month = parse_month_ref(ref)
        if month is not None:
            period = await find_or_create_period_for_month(
                db, month[0], month[1], allow_create=can_create_periods
            )
            if period is None:
                missing_months.append(month_label(month[0], month[1]))
            else:
                await _append(period.id)
            continue
        try:
            period_id = ref if isinstance(ref, UUID) else UUID(str(ref))
        except (ValueError, AttributeError, TypeError):
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unrecognized period reference: {ref}. "
                    "Use a period id or a YYYY-MM month."
                ),
            )
        found = (
            await db.execute(
                select(KPIPeriod).where(
                    KPIPeriod.id == period_id,
                    KPIPeriod.is_deleted.is_(False),
                )
            )
        ).scalars().first()
        if found is None:
            unknown_ids.append(str(period_id))
        else:
            await _append(found.id)
    if unknown_ids:
        raise HTTPException(
            status_code=422,
            detail=(
                "Period(s) not found: " + ", ".join(unknown_ids)
                + ". Create the period first, then link it to the formula."
            ),
        )
    if missing_months:
        raise HTTPException(
            status_code=422,
            detail=(
                "Period(s) missing: " + ", ".join(missing_months)
                + ". Ask an admin to create them, or save with an account "
                "that holds kpi.create so they are created automatically."
            ),
        )
    return resolved


async def scored_unlink_blocks(
    db: AsyncSession, formula: KPIFormula, leaving_period_ids
) -> list[dict]:
    """Periods in ``leaving_period_ids`` that already have scores
    for this formula. A score counts via the linked-period id, or —
    for pre-link legacy records — via a period_start inside the
    period window. Returns [{period_id, period_label, record_count}]."""
    leaving = list(dict.fromkeys(leaving_period_ids or []))
    if not leaving:
        return []
    periods = (
        await db.execute(
            select(KPIPeriod).where(KPIPeriod.id.in_(leaving))
        )
    ).scalars().all()
    blocked = []
    for period in periods:
        count = (
            await db.execute(
                select(func.count())
                .select_from(KPIRecord)
                .where(
                    KPIRecord.formula_id == formula.id,
                    KPIRecord.is_deleted.is_(False),
                    (
                        (KPIRecord.kpi_period_id == period.id)
                        | (
                            KPIRecord.kpi_period_id.is_(None)
                            & (KPIRecord.period_start >= period.start_date)
                            & (KPIRecord.period_start < period.end_date)
                        )
                    ),
                )
            )
        ).scalar_one()
        if count:
            blocked.append({
                "period_id": str(period.id),
                "period_label": period.label,
                "record_count": int(count),
            })
    return blocked


def _positions_overlap(a: KPIFormula, b: KPIFormula) -> bool:
    """Mirror of the engine's position fallback: a NULL position_id
    is a wildcard (department-wide), so it overlaps any position;
    two specific positions overlap on the same id or the same name."""
    if a.position_id is None or b.position_id is None:
        return True
    if a.position_id == b.position_id:
        return True
    return bool(a.position_name and a.position_name == b.position_name)


def _regions_overlap(a: KPIFormula, b: KPIFormula) -> bool:
    """The engine only filters region when the user has one, so two
    formulas overlap on region unless both carry different regions."""
    return (
        a.region_town is None
        or b.region_town is None
        or a.region_town == b.region_town
    )


def formulas_overlap(a: KPIFormula, b: KPIFormula) -> bool:
    """True when one user could match both formulas under the
    engine's own matcher (department + position fallback + level +
    region). Scope is reported, not matched: the engine itself
    filters on department_id, never on the scope enum."""
    if not a.department_id or not b.department_id:
        return False
    if a.department_id != b.department_id:
        return False
    if str(a.user_level or "").strip().upper() != str(b.user_level or "").strip().upper():
        return False
    if not _positions_overlap(a, b):
        return False
    if not _regions_overlap(a, b):
        return False
    return True


async def find_overlap_warnings(
    db: AsyncSession, formula: KPIFormula, period_ids
) -> list[dict]:
    """Non-blocking save warnings: other ACTIVE, open formulas with
    a current link to one of ``period_ids`` whose audience overlaps
    this formula's. The saved formula itself may still be DRAFT —
    the conflict materializes at activation, which is exactly when
    the warning must already have been seen."""
    if not period_ids or formula.closed_at is not None:
        return []
    period_rows = (
        await db.execute(
            select(KPIPeriod).where(KPIPeriod.id.in_(list(period_ids)))
        )
    ).scalars().all()
    labels = {period.id: period.label for period in period_rows}
    candidates = (
        await db.execute(
            select(KPIFormula)
            .join(
                KPIFormulaPeriod,
                KPIFormulaPeriod.formula_id == KPIFormula.id,
            )
            .where(
                KPIFormulaPeriod.period_id.in_(list(period_ids)),
                KPIFormulaPeriod.is_current.is_(True),
                KPIFormula.id != formula.id,
                KPIFormula.status == FormulaStatus.ACTIVE,
                KPIFormula.is_active.is_(True),
                KPIFormula.is_deleted.is_(False),
                KPIFormula.closed_at.is_(None),
            )
        )
    ).scalars().all()
    warnings = []
    for other in candidates:
        if not formulas_overlap(formula, other):
            continue
        link_periods = (
            await db.execute(
                select(KPIFormulaPeriod.period_id).where(
                    KPIFormulaPeriod.formula_id == other.id,
                    KPIFormulaPeriod.is_current.is_(True),
                    KPIFormulaPeriod.period_id.in_(list(period_ids)),
                )
            )
        ).scalars().all()
        position_label = (
            formula.position_name or other.position_name or "All positions"
        )
        for period_id in link_periods:
            warnings.append({
                "period_id": str(period_id),
                "period_label": labels.get(period_id, str(period_id)),
                "conflicting_formula_id": str(other.id),
                "conflicting_formula_name": other.name,
                "shared_scope": {
                    "department_id": str(formula.department_id),
                    "user_level": formula.user_level,
                    "position": position_label,
                },
            })
    warnings.sort(key=lambda w: (w["period_label"], w["conflicting_formula_name"]))
    return warnings


__all__ = [
    "MAX_CUSTOM_RANGE_MONTHS",
    "expand_month_range",
    "find_or_create_period_for_month",
    "find_overlap_warnings",
    "find_period_for_month",
    "formulas_overlap",
    "month_bounds",
    "month_label",
    "parse_month_ref",
    "resolve_period_refs",
    "scored_unlink_blocks",
]
