"""Engine helper functions for KPI scoring.

These helpers isolate scoring strategies used by the KPI calculation engine:
- Target-based scoring (e.g., sales targets, defect counts)
- Deduction-based scoring (rule-driven penalties)
"""

import math
import re
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.kpi.models import KPIScoringRuleItem
from app.modules.targets.models import Target


def standardize_key(key_str: str) -> str:
    """Normalize category/rule names for Excel vs workflow matching."""
    if not key_str:
        return ""
    return re.sub(r"\s+", " ", str(key_str).strip().lower())


def _find_assigned_target(targets, std_cat_name: str, dept_id):
    """Find the best matching target, preferring department-specific over global."""
    assigned_target = None
    eligible = [
        t for t in targets
        if not getattr(t, "is_deleted", False)
        and (
            not hasattr(t, "status")
            or getattr(getattr(t, "status", None), "value", getattr(t, "status", None)) == "ACTIVE"
        )
    ]
    for t in sorted(
        eligible,
        key=lambda target: (
            target.department_id is None,
            getattr(target, "created_at", None) or "",
            str(getattr(target, "id", "")),
        ),
    ):
        if standardize_key(t.name) == std_cat_name:
            if t.department_id == dept_id:
                return t
            if not assigned_target:
                assigned_target = t
    return assigned_target


async def calculate_target_based_score(
    db: AsyncSession,
    dept_id,
    target_month_str: str,
    std_cat_name: str,
    max_score: float,
    final_merged_data: dict[str, float],
) -> float:
    """Return the score for a TARGET-based category."""
    stmt_target = select(Target).where(
        Target.target_month == target_month_str,
        Target.is_deleted == False,
        or_(Target.department_id == dept_id, Target.department_id.is_(None)),
    )
    targets = (await db.execute(stmt_target)).scalars().all()

    assigned_target = _find_assigned_target(targets, std_cat_name, dept_id)

    if not assigned_target or not assigned_target.target_value or assigned_target.target_value <= 0:
        return 0.0

    target_val = float(assigned_target.target_value)
    if std_cat_name not in final_merged_data:
        return 0.0
    actual_val = float(final_merged_data[std_cat_name])

    metric_type_attr = getattr(assigned_target, "metric_type", "")
    metric_type_val = getattr(metric_type_attr, "value", str(metric_type_attr)).upper()
    is_lower_better = "COUNT" in metric_type_val

    if math.isclose(target_val, 0.0):
        achievement_ratio = 1.0
    else:
        if is_lower_better:
            achievement_ratio = target_val / actual_val if actual_val > 0 else 1.0
        else:
            achievement_ratio = actual_val / target_val

    achievement_ratio = min(1.0, max(0.0, achievement_ratio))
    return max_score * achievement_ratio


def calculate_deduction_based_score(
    cat_name: str,
    cat_rules: list[KPIScoringRuleItem],
    final_merged_data: dict[str, float],
    max_score: float,
) -> float:
    """Return the score for a DEDUCTION-based category."""
    if not cat_rules:
        return max_score
    from app.modules.kpi.engine import KPICalculationEngine

    return KPICalculationEngine.evaluate_db_rules(cat_name, cat_rules, final_merged_data, max_score)


async def score_formula_categories(
    db: AsyncSession,
    formula: Any,
    dept_id,
    target_month_str: str,
    final_merged_data: dict[str, float],
) -> float:
    """Apply each formula category's scoring strategy and return the weighted total."""
    total_weighted_score = 0.0
    categories = formula.categories or []
    scoring_rules = list(getattr(formula, "scoring_rules", None) or [])

    for category in categories:
        cat_name = category.get("name", "")
        std_cat_name = standardize_key(cat_name)

        raw_weight = float(category.get("weight", 0.0))
        cat_weight = raw_weight if raw_weight <= 1.0 else raw_weight / 100.0
        max_score = float(category.get("max_score", 5.0))
        eval_type = category.get("evaluation_type", "DEDUCTION")

        if eval_type == "TARGET":
            cat_actual_score = await calculate_target_based_score(
                db=db,
                dept_id=dept_id,
                target_month_str=target_month_str,
                std_cat_name=std_cat_name,
                max_score=max_score,
                final_merged_data=final_merged_data,
            )
        else:
            cat_rules = [
                r for r in scoring_rules
                if standardize_key(getattr(r, "category_name", "")) == std_cat_name
            ]
            cat_actual_score = calculate_deduction_based_score(
                cat_name=cat_name,
                cat_rules=cat_rules,
                final_merged_data=final_merged_data,
                max_score=max_score,
            )

        total_weighted_score += cat_actual_score * cat_weight

    return total_weighted_score
