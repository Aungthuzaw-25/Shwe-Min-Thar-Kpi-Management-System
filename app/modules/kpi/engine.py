from __future__ import annotations

import logging
import operator
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, desc, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.core.timezone import as_application_time
from app.modules.kpi.engine_helpers import score_formula_categories, standardize_key
from app.modules.kpi.models import (
    FormulaStatus,
    KPIAssignment,
    KPIFormula,
    KPIHistory,
    KPILevelSetting,
    KPIPeriod,
    KPIPeriodStatus,
    KPIRecord,
    KPIScoringRuleItem,
    KPIStatus,
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range, normalize_calendar_range
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import User

# Configure Module Logger for Observability
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class KPICalculationContext:
    record_id: UUID
    assignment_id: UUID | None = None
    period_id: UUID | None = None
    formula_id: UUID | None = None
    user_id: UUID | None = None


@dataclass(frozen=True)
class ResolvedKPICalculationContext:
    user: User
    record: KPIRecord
    period: KPIPeriod
    formula: KPIFormula
    period_start: datetime
    period_end: datetime


# Python Operators Mapping for dynamic mathematical parsing
OPERATORS = {
    ">=": operator.ge, "<=": operator.le, 
    "==": operator.eq, ">": operator.gt, "<": operator.lt
}

# 🌟 ENTERPRISE FIX: Extract hardcoded localization strings to maintainable constants
LATE_SUBMISSION_KEYWORDS = ["late", "အချိန်", "နောက်ကျ"]
DEFAULT_LATE_CAT_NAME = "အချိန်မှီမတင်ပြခြင်း"
KPI_RECORD_NOT_FOUND = "KPI record was not found."


def _parse_numeric(value, context: str) -> float | None:
    try:
        return float(value)
    except (ValueError, TypeError):
        logger.warning("Invalid numeric value for %s", context)
        return None


def _group_scoring_rules(rules):
    grouped = {}
    for rule in rules:
        key = standardize_key(rule.rule_name)
        grouped.setdefault(key, []).append(rule)
    return grouped


def _rule_deduction(actual_value: float, rule) -> float:
    operation = OPERATORS.get(rule.operator)
    if not operation:
        logger.error("Unsupported operator '%s' in rule '%s'", rule.operator, rule.rule_name)
        return 0.0
    target = _parse_numeric(rule.target_value, f"rule {rule.rule_name}")
    deduction = _parse_numeric(rule.deduct_score, f"deduction {rule.rule_name}")
    if target is None or deduction is None or not operation(actual_value, target):
        return 0.0
    return deduction


def _score_rule_group(actual_value: float, rules) -> float:
    return max((_rule_deduction(actual_value, rule) for rule in rules), default=0.0)


def _merge_kpi_data(deduction_values, deduction_counts, manual_data):
    merged = {}
    for key, values in deduction_values.items():
        value = min(values) if all(item <= 100 for item in values) else sum(values)
        normalized = standardize_key(key)
        merged[normalized] = merged.get(normalized, 0.0) + value
    for key, value in deduction_counts.items():
        normalized = standardize_key(key)
        merged[normalized] = merged.get(normalized, 0.0) + float(value)
    for key, value in manual_data.items():
        try:
            normalized = standardize_key(key)
            merged[normalized] = merged.get(normalized, 0.0) + float(value)
        except (ValueError, TypeError):
            logger.warning("Skipping non-numeric manual data for key %s", key)
    return merged


def _normalize_user_level(value):
    normalized = str(value or "").strip().upper().replace("LEVEL ", "L")
    return normalized if normalized in {"L0", "L1", "L2"} else None


async def _find_fallback_formula(db, user, target_month: str):
    department_id, user_level = _active_membership_context(user)
    if not department_id and _has_active_position(user):
        department_id = user.position.department_id
    if not department_id or not user_level:
        return None
    position_filter = _fallback_position_filter(user)
    statement = select(KPIFormula).options(
        selectinload(KPIFormula.scoring_rules)
    ).where(
        KPIFormula.department_id == department_id,
        KPIFormula.user_level == user_level,
        position_filter,
        KPIFormula.status == FormulaStatus.ACTIVE,
        KPIFormula.is_active == True,
        KPIFormula.is_deleted == False,
        or_(
            KPIFormula.effective_month.is_(None),
            KPIFormula.effective_month <= target_month,
        ),
    )
    if user.region_town:
        statement = statement.where(KPIFormula.region_town == user.region_town)
    statement = statement.order_by(
        desc(KPIFormula.effective_month), desc(KPIFormula.version), KPIFormula.id
    )
    formulas = (await db.execute(statement)).scalars().all()
    _validate_fallback_formula_priority(formulas)
    return formulas[0] if formulas else None


def _active_membership_context(user) -> tuple[UUID | None, str | None]:
    memberships = [
        membership
        for membership in (user.user_departments or [])
        if not getattr(membership, "is_deleted", False)
    ]
    if not memberships:
        return None, None
    membership = memberships[0]
    return membership.department_id, _normalize_user_level(membership.user_level)


def _has_active_position(user) -> bool:
    return bool(
        user.position
        and user.position.is_active
        and not user.position.is_deleted
    )


def _fallback_position_filter(user):
    if user.position_id and user.position:
        return or_(
            KPIFormula.position_id == user.position_id,
            and_(
                KPIFormula.position_id.is_(None),
                KPIFormula.position_name == user.position.name,
            ),
        )
    position_name = user.position.name if user.position else "Unknown"
    return and_(
        KPIFormula.position_id.is_(None),
        KPIFormula.position_name == position_name,
    )


def _validate_fallback_formula_priority(formulas) -> None:
    if (
        len(formulas) > 1
        and formulas[0].effective_month == formulas[1].effective_month
        and formulas[0].version == formulas[1].version
    ):
        raise ValueError("Multiple equally-prioritized active KPI formulas matched the user.")


async def _collect_deductions(db, user, formula, period_start, period_end):
    start = as_application_time(period_start)
    end = as_application_time(period_end)
    created_start = start.astimezone(UTC).replace(tzinfo=None)
    created_end = end.astimezone(UTC).replace(tzinfo=None)
    date_filter = or_(
        and_(Task.due_date >= start, Task.due_date < end),
        and_(
            Task.due_date.is_(None),
            Task.created_at >= created_start,
            Task.created_at < created_end,
        ),
    )
    l2 = select(TaskReview.task_id, TaskReview.comments).join(Task, TaskReview.task_id == Task.id).where(
        Task.owner_id == user.id,
        Task.is_deleted == False,
        TaskReview.is_deleted == False,
        TaskReview.action.in_(["REJECTED", "REJECTED_OVERRIDE"]),
        TaskReview.reviewer_level.in_(["L1", "Level 1"]),
        date_filter,
    )
    l1 = select(TaskReview.task_id, TaskReview.comments).join(Task, TaskReview.task_id == Task.id).where(
        TaskReview.task_id.in_(select(TaskReview.task_id).where(
            TaskReview.reviewer_id == user.id,
            TaskReview.action == "APPROVED",
            TaskReview.reviewer_level.in_(["L1", "Level 1"]),
        ).scalar_subquery()),
        TaskReview.reviewer_level.in_(["L0", "Level 0"]),
        TaskReview.action.in_(["REJECTED", "REJECTED_OVERRIDE"]),
        Task.is_deleted == False,
        TaskReview.is_deleted == False,
        date_filter,
    )
    comments = await _rejection_comments(db, l2, l1)
    counts, values, rejected_task_ids = _parse_rejection_comments(comments)
    late_count = await _late_task_count(
        db, user, date_filter, rejected_task_ids
    )
    if late_count:
        late_name = _late_category_name(formula)
        counts[late_name] = counts.get(late_name, 0) + late_count
    return counts, values


async def _rejection_comments(db, level_two_statement, level_one_statement):
    level_two_comments = (await db.execute(level_two_statement)).all()
    level_one_comments = (await db.execute(level_one_statement)).all()
    return level_two_comments + level_one_comments


def _parse_rejection_comments(comments):
    counts, values = {}, {}
    rejected_task_ids = set()
    for task_id, comment in comments:
        if not comment:
            continue
        rejected_task_ids.add(task_id)
        match = re.search(r"\[KPI Deduction:([^\]]+)\]", comment)
        if not match:
            continue
        rule_name = match.group(1).strip()
        value_match = re.search(r"\[Value:\s*([\d\.]+)\]", comment)
        if value_match:
            value = _parse_numeric(value_match.group(1), f"comment {rule_name}")
            if value is not None:
                values.setdefault(rule_name, []).append(value)
        else:
            counts[rule_name] = counts.get(rule_name, 0) + 1
    return counts, values, rejected_task_ids


async def _late_task_count(db, user, date_filter, rejected_task_ids) -> int:
    late_query = select(func.count(Task.id)).where(
        Task.owner_id == user.id, Task.is_late == True, Task.is_deleted == False,
        date_filter,
        _late_task_rejection_filter(rejected_task_ids),
    )
    return (await db.execute(late_query)).scalar() or 0


def _late_task_rejection_filter(rejected_task_ids):
    if rejected_task_ids:
        return ~Task.id.in_(rejected_task_ids)
    return True


def _late_category_name(formula) -> str:
    return next(
        (
            category.get("name", "")
            for category in formula.categories
            if any(
                keyword in category.get("name", "").lower()
                for keyword in LATE_SUBMISSION_KEYWORDS
            )
        ),
        DEFAULT_LATE_CAT_NAME,
    )


def _validate_context_record_ids(
    context: KPICalculationContext, record: KPIRecord
) -> None:
    if context.user_id is not None and context.user_id != record.user_id:
        raise ValueError("KPI record does not belong to the requested user.")
    if context.period_id is not None and context.period_id != record.kpi_period_id:
        raise ValueError("KPI record does not belong to the requested period.")
    if context.formula_id is not None and context.formula_id != record.formula_id:
        raise ValueError("KPI record does not use the requested formula.")


def _validate_explicit_record_entities(
    record: KPIRecord,
    user: User | None,
    period: KPIPeriod | None,
    formula: KPIFormula | None,
) -> None:
    if user is None or user.is_deleted or not user.is_active:
        raise ValueError("KPI record user is unavailable.")
    if period is None or period.is_deleted:
        raise ValueError("KPI record must reference an available KPI period.")
    if period.status in {
        KPIPeriodStatus.FINALIZED,
        KPIPeriodStatus.LOCKED,
        KPIPeriodStatus.CANCELLED,
    }:
        raise ValueError("KPI calculation is not allowed for a finalized, locked, or cancelled period.")
    if (
        formula is None
        or formula.is_deleted
        or not formula.is_active
        or formula.status != FormulaStatus.ACTIVE
    ):
        raise ValueError("KPI record formula is unavailable.")
    if record.is_frozen or record.is_overridden:
        raise ValueError("Frozen or overridden KPI records cannot be recalculated.")


def _validated_record_period_range(
    record: KPIRecord, period: KPIPeriod
):
    period_range = normalize_calendar_range(period.start_date, period.end_date)
    if period_range.start_inclusive is None or period_range.end_exclusive is None:
        raise ValueError("KPI period boundaries are incomplete.")
    boundaries_match = (
        as_application_time(period.start_date) == period_range.start_inclusive
        and as_application_time(period.end_date) == period_range.end_exclusive
        and as_application_time(record.period_start) == period_range.start_inclusive
        and as_application_time(record.period_end) == period_range.end_exclusive
    )
    if not boundaries_match:
        raise ValueError("KPI record and period must use matching calendar boundaries.")
    return period_range


async def _validate_record_assignment(
    db: AsyncSession,
    context: KPICalculationContext,
    record: KPIRecord,
) -> None:
    if context.assignment_id is None:
        return
    assignment = await db.get(KPIAssignment, context.assignment_id)
    if assignment is None or assignment.is_deleted:
        raise ValueError("KPI assignment is unavailable.")
    assignment_matches_record = (
        assignment.user_id == record.user_id
        and assignment.kpi_period_id == record.kpi_period_id
        and assignment.kpi_formula_id == record.formula_id
    )
    if not assignment_matches_record:
        raise ValueError("KPI assignment does not match the selected record.")


def _validate_formula_effective_month(
    formula: KPIFormula, effective_month: str
) -> None:
    if formula.effective_month and formula.effective_month > effective_month:
        raise ValueError("KPI record formula is not effective for the selected period.")


async def _save_kpi_record(
    db,
    user,
    formula,
    period,
    period_type,
    period_start,
    period_end,
    total_score,
    percentage,
    explicit_record_id: UUID | None = None,
):
    await _ensure_period_open(db, period)
    statement = _kpi_record_statement(
        user, period_type, period_start, explicit_record_id
    )
    record = (await db.execute(statement)).scalars().first()
    if explicit_record_id is not None:
        _validate_saved_record_context(record, user, formula, period)
    if record:
        if record.is_frozen or record.is_overridden:
            if explicit_record_id is not None:
                raise ValueError("Frozen or overridden KPI records cannot be recalculated.")
            return
        _update_kpi_record(
            db, record, formula, period, period_end, total_score, percentage,
            explicit_record_id,
        )
        return
    if explicit_record_id is not None:
        raise ValueError(KPI_RECORD_NOT_FOUND)
    db.add(
        KPIRecord(
            user_id=user.id,
            department_id=formula.department_id,
            formula_id=formula.id,
            period_type=period_type,
            period_start=period_start,
            period_end=period_end,
            kpi_period_id=period.id if period else None,
            weighted_score=total_score,
            raw_score=total_score,
            percentage=percentage,
            status_bucket=KPICalculationEngine.get_grade(percentage),
        )
    )


async def _ensure_period_open(db, period) -> None:
    if period is not None:
        locked_period = (await db.execute(
            select(KPIPeriod).where(KPIPeriod.id == period.id).with_for_update()
        )).scalars().first()
        if locked_period and locked_period.status in {
            KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED, KPIPeriodStatus.CANCELLED
        }:
            raise ValueError("KPI calculation is not allowed for a finalized, locked, or cancelled period.")


def _kpi_record_statement(user, period_type, period_start, explicit_record_id):
    if explicit_record_id is not None:
        return select(KPIRecord).where(
            KPIRecord.id == explicit_record_id,
            KPIRecord.is_deleted.is_(False),
        ).with_for_update()
    return select(KPIRecord).where(
        KPIRecord.user_id == user.id,
        KPIRecord.period_type == period_type,
        KPIRecord.period_start == period_start,
        KPIRecord.is_deleted == False,
    ).with_for_update()


def _validate_saved_record_context(record, user, formula, period) -> None:
    if record is None:
        raise ValueError(KPI_RECORD_NOT_FOUND)
    matches_context = (
        record.user_id == user.id
        and record.formula_id == formula.id
        and record.kpi_period_id == period.id
    )
    if not matches_context:
        raise ValueError("KPI record context changed during calculation.")


def _recalculation_provenance_changes(record, formula, period, period_end):
    changes = []
    values = (
        ("department_id", record.department_id, formula.department_id),
        ("formula_id", record.formula_id, formula.id),
        ("kpi_period_id", record.kpi_period_id, period.id if period else None),
        ("period_end", record.period_end, period_end),
    )
    for field_name, old_value, new_value in values:
        values_differ = old_value != new_value
        if field_name == "period_end" and old_value is not None and new_value is not None:
            values_differ = (
                as_application_time(old_value) != as_application_time(new_value)
            )
        if values_differ:
            changes.append(f"{field_name}: {old_value} -> {new_value}")
    return changes


def _record_recalculation_history(
    db, record, old_score, old_percentage, old_status, provenance_changes
) -> None:
    if not (
        old_score != float(record.weighted_score)
        or old_percentage != float(record.percentage)
        or old_status != record.status_bucket
        or provenance_changes
    ):
        return
    reason = (
        "Automatic KPI recalculation"
        if not provenance_changes
        else "Automatic KPI recalculation; " + "; ".join(provenance_changes)
    )
    db.add(
        KPIHistory(
            kpi_record_id=record.id,
            old_percentage=old_percentage,
            new_percentage=float(record.percentage),
            old_weighted_score=old_score,
            new_weighted_score=float(record.weighted_score),
            old_status=old_status,
            new_status=record.status_bucket,
            reason=reason,
        )
    )


def _update_kpi_record(
    db, record, formula, period, period_end, total_score, percentage,
    explicit_record_id,
) -> None:
    old_score = float(record.weighted_score or 0.0)
    old_percentage = float(record.percentage or 0.0)
    old_status = record.status_bucket
    provenance_changes = _recalculation_provenance_changes(
        record, formula, period, period_end
    )
    record.weighted_score = total_score
    record.raw_score = total_score
    record.percentage = percentage
    record.status_bucket = KPICalculationEngine.get_grade(percentage)
    record.department_id = formula.department_id
    record.formula_id = formula.id
    record.period_end = period_end
    if explicit_record_id is None:
        record.kpi_period_id = period.id if period else None
    _record_recalculation_history(
        db, record, old_score, old_percentage, old_status, provenance_changes
    )


def formula_max_score(formula) -> float:
    return sum(
        float(category.get("max_score", 5.0))
        * (
            float(category.get("weight", 0.0))
            if float(category.get("weight", 0.0)) <= 1
            else float(category.get("weight", 0.0)) / 100
        )
        for category in (formula.categories or [])
    )


# ==============================================================================
# 1. KPI Aggregation Engine 
# ==============================================================================
async def _resolve_default_formula_and_period(
    db: AsyncSession,
    user: User | None,
    period_start: datetime | None,
    period_end: datetime | None,
    period_type: PeriodType | None,
):
    if user is None or period_start is None or period_end is None or period_type is None:
        raise ValueError("User, date boundaries, and period type are required.")
    return await KPICalculationEngine.get_applicable_formula_and_period(
        db, user, period_start
    )


class KPICalculationEngine:
    
    @staticmethod
    def standardize_key(key_str: str) -> str:
        """
        🌟 SENIOR FIX: String Matching Bug
        Space အပိုများကို ဖယ်ရှား၍ စာလုံးအသေးသို့ ပြောင်းလဲပေးမည်။ 
        Excel Column များနှင့် Rule Name များ လွဲချော်မှုကို အပြည့်အဝ ကာကွယ်ပေးသည်။
        """
        return standardize_key(key_str)

    @staticmethod
    def get_grade(percentage: float) -> KPIStatus:
        """Determines the KPI Grade based on percentage performance."""
        if percentage >= 90.0: return KPIStatus.EXCELLENT
        elif percentage >= 80.0: return KPIStatus.VERY_GOOD
        elif percentage >= 60.0: return KPIStatus.GOOD
        elif percentage >= 40.0: return KPIStatus.FAIR
        else: return KPIStatus.POOR

    @staticmethod
    def evaluate_db_rules(cat_name: str, rules: list[KPIScoringRuleItem], evaluated_data: dict, max_score: float = 5.0) -> float:
        """
        Checks BOTH the Category Name (from Excel) AND the Rule Name (from System Workflow)
        to ensure no deductions are missed. Uses highest applicable deduction per rule.
        """
        category_key = standardize_key(cat_name)
        base_value = 0.0
        if category_key in evaluated_data:
            base_value = _parse_numeric(evaluated_data[category_key], category_key) or 0.0
        total_deduction = 0.0
        for rule_name, rule_items in _group_scoring_rules(rules).items():
            actual_value = base_value
            if rule_name and rule_name in evaluated_data:
                rule_value = _parse_numeric(evaluated_data[rule_name], rule_name)
                if rule_value is not None:
                    actual_value = max(base_value, rule_value)
            total_deduction += _score_rule_group(actual_value, rule_items)
        return max(0.0, float(max_score) - total_deduction)

    @staticmethod
    async def get_applicable_formula_and_period(db: AsyncSession, user: User, target_date: datetime) -> tuple[KPIFormula | None, KPIPeriod | None]:
        """
        🌟 Fully Relational Connection:
        Period ကို ရှာမည် -> ထို Period တွင် Assign လုပ်ထားသော Assignment ကို ရှာမည် -> Formula ကို ဆွဲထုတ်မည်။
        """
        target_date = as_application_time(target_date)
        stmt_period = select(KPIPeriod).where(
            KPIPeriod.start_date <= target_date,
            KPIPeriod.end_date > target_date,
            KPIPeriod.is_deleted == False
        ).order_by(KPIPeriod.start_date.desc(), KPIPeriod.id)
        periods = (await db.execute(stmt_period)).scalars().all()
        if len(periods) > 1:
            raise ValueError("Multiple active KPI periods overlap the calculation date.")
        period = periods[0] if periods else None
        
        if period:
            stmt_assign = select(KPIAssignment).where(
                KPIAssignment.user_id == user.id,
                KPIAssignment.kpi_period_id == period.id,
                KPIAssignment.is_deleted == False
            ).order_by(KPIAssignment.created_at.desc(), KPIAssignment.id)
            assignments = (await db.execute(stmt_assign)).scalars().all()
            if len(assignments) > 1:
                raise ValueError("Multiple active KPI assignments matched the user and period.")
            assignment = assignments[0] if assignments else None
            
            if assignment:
                stmt_form = select(KPIFormula).options(selectinload(KPIFormula.scoring_rules)).where(
                    KPIFormula.id == assignment.kpi_formula_id,
                    KPIFormula.status == FormulaStatus.ACTIVE,
                    KPIFormula.is_active == True,
                    KPIFormula.is_deleted == False,
                    or_(
                        KPIFormula.effective_month.is_(None),
                        KPIFormula.effective_month <= target_date.strftime("%Y-%m"),
                    ),
                )
                formula = (await db.execute(stmt_form)).scalars().first()
                if formula:
                    return formula, period

        return await _find_fallback_formula(db, user, target_date.strftime("%Y-%m")), period

    @staticmethod
    async def resolve_explicit_calculation_context(
        db: AsyncSession,
        context: KPICalculationContext,
    ) -> ResolvedKPICalculationContext:
        record = (
            await db.execute(
                select(KPIRecord)
                .options(
                    selectinload(KPIRecord.user),
                    selectinload(KPIRecord.period),
                    selectinload(KPIRecord.formula).selectinload(KPIFormula.scoring_rules),
                )
                .where(
                    KPIRecord.id == context.record_id,
                    KPIRecord.is_deleted.is_(False),
                )
            )
        ).scalars().first()
        if record is None:
            raise ValueError(KPI_RECORD_NOT_FOUND)
        _validate_context_record_ids(context, record)

        user = record.user
        period = record.period
        formula = record.formula
        _validate_explicit_record_entities(record, user, period, formula)
        period_range = _validated_record_period_range(record, period)
        await _validate_record_assignment(db, context, record)
        effective_month = period_range.start_inclusive.strftime("%Y-%m")
        _validate_formula_effective_month(formula, effective_month)

        return ResolvedKPICalculationContext(
            user=user,
            record=record,
            period=period,
            formula=formula,
            period_start=period_range.start_inclusive,
            period_end=period_range.end_exclusive,
        )

    @staticmethod
    async def execute_kpi_calculation(
        db: AsyncSession,
        user: User | None = None,
        period_start: datetime | None = None,
        period_end: datetime | None = None,
        period_type: PeriodType | None = None,
        task_data: dict | None = None,
        *,
        context: KPICalculationContext | None = None,
        commit: bool = True,
    ):
        """
        Core Engine: Aggregates tasks, parses text-based deductions, calculates target completion, 
        and updates or creates the final KPI Record securely with Transaction rollback protection.
        """
        user_id = user.id if user is not None else None
        if user_id is None and context is not None:
            user_id = context.user_id
        try:
            explicit_record_id = None
            if context is not None:
                resolved = await KPICalculationEngine.resolve_explicit_calculation_context(
                    db, context
                )
                user = resolved.user
                formula = resolved.formula
                period = resolved.period
                period_start = resolved.period_start
                period_end = resolved.period_end
                period_type = resolved.record.period_type
                explicit_record_id = resolved.record.id
            else:
                formula, period = await _resolve_default_formula_and_period(
                    db, user, period_start, period_end, period_type
                )
            if not formula:
                logger.info("No active formula found for user %s", user_id)
                return None
            counts, values = await _collect_deductions(db, user, formula, period_start, period_end)
            merged_data = _merge_kpi_data(values, counts, task_data or {})
            total_score = await score_formula_categories(
                db,
                formula,
                formula.department_id,
                as_application_time(period_start).strftime("%Y-%m"),
                merged_data,
            )
            weighted_max = formula_max_score(formula)
            percentage = 0.0 if weighted_max <= 0 else min(100.0, max(0.0, total_score / weighted_max * 100.0))
            if explicit_record_id is None:
                await _save_kpi_record(
                    db, user, formula, period, period_type, period_start, period_end,
                    total_score, percentage,
                )
            else:
                await _save_kpi_record(
                    db, user, formula, period, period_type, period_start, period_end,
                    total_score, percentage, explicit_record_id=explicit_record_id,
                )
            if commit:
                await db.commit()
            return resolved.record if explicit_record_id is not None else None
        except Exception:
            await db.rollback()
            logger.exception("Critical error calculating KPI for user %s", user_id)
            raise

    @staticmethod
    async def calculate_monthly_kpi_for_user(db: AsyncSession, user: User, target_date: datetime, task_data: dict = None):
        """Helper to invoke monthly calculation logic correctly parsing dates"""
        period_range = calendar_period_range(PeriodType.MONTHLY, target_date)

        await KPICalculationEngine.execute_kpi_calculation(
            db,
            user,
            period_range.start_inclusive,
            period_range.end_exclusive,
            PeriodType.MONTHLY,
            task_data,
        )


# ==============================================================================
# 2. Support Helper Functions
# ==============================================================================
async def is_level_kpi_enabled(db: AsyncSession, level_name: str, department_id: UUID | None = None) -> bool:
    """Checks if KPI evaluation is administratively unblocked for a user level."""
    stmt = select(KPILevelSetting).where(KPILevelSetting.level_name == level_name)
    
    if department_id:
        stmt = stmt.where(KPILevelSetting.department_id == department_id)
        
    setting = (await db.execute(stmt)).scalars().first()
    return setting.is_enabled if setting else False


# ==============================================================================
# 3. Master Routing Operations
# ==============================================================================
async def trigger_kpi_recalculation(
    db: AsyncSession,
    user_level: str,
    user_id: UUID,
    target_date: datetime,
    department_id: UUID | None = None,
    *,
    commit: bool = True,
):
    """Entry Point for on-the-fly recalculations whenever a task is processed."""
    
    stmt = select(User).options(
        selectinload(User.user_departments),
        selectinload(User.position)
    ).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    
    if not user: return

    if not department_id:
        if user.user_departments:
            department_id = user.user_departments[0].department_id
        elif user.position:
            department_id = user.position.department_id

    if not department_id: return
    if not await is_level_kpi_enabled(db, user_level, department_id): return

    period_range = calendar_period_range(PeriodType.MONTHLY, target_date)
    await KPICalculationEngine.execute_kpi_calculation(
        db,
        user,
        period_range.start_inclusive,
        period_range.end_exclusive,
        PeriodType.MONTHLY,
        commit=commit,
    )

async def calculate_custom_period_kpi(db: AsyncSession, user_id: UUID, start_date: datetime, end_date: datetime, task_data: dict = None):
    """Generates ad-hoc KPI records for custom manual evaluation scopes."""
    stmt = select(User).options(
        selectinload(User.user_departments),
        selectinload(User.position)
    ).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    
    if not user: return
    
    await KPICalculationEngine.execute_kpi_calculation(db, user, start_date, end_date, PeriodType.MONTHLY, task_data)