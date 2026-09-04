from types import SimpleNamespace

from app.modules.kpi.engine import KPICalculationEngine, formula_max_score
from app.modules.kpi.engine_helpers import (
    _find_assigned_target,
    calculate_deduction_based_score,
    score_formula_categories,
    standardize_key,
)
from app.modules.kpi.models import KPIStatus


def test_standardize_key_normalizes_spaces_and_case():
    assert KPICalculationEngine.standardize_key("  Late Submission  ") == "late submission"


def test_get_grade_returns_expected_status():
    assert KPICalculationEngine.get_grade(95.0) == KPIStatus.EXCELLENT
    assert KPICalculationEngine.get_grade(80.0) == KPIStatus.VERY_GOOD
    assert KPICalculationEngine.get_grade(60.0) == KPIStatus.GOOD
    assert KPICalculationEngine.get_grade(40.0) == KPIStatus.FAIR
    assert KPICalculationEngine.get_grade(10.0) == KPIStatus.POOR


def test_evaluate_db_rules_uses_highest_applicable_deduction():
    rules = [
        SimpleNamespace(rule_name="Late Submission", operator=">=", target_value="2", deduct_score="1.5"),
        SimpleNamespace(rule_name="Late Submission", operator=">=", target_value="5", deduct_score="2.5"),
    ]

    score = KPICalculationEngine.evaluate_db_rules(
        "Late Submission",
        rules,
        {"late submission": 7},
        max_score=5.0,
    )

    assert score == 2.5


def test_standardize_key_helper_matches_engine():
    assert standardize_key("  Late Submission  ") == KPICalculationEngine.standardize_key("  Late Submission  ")


def test_find_assigned_target_prefers_department_match():
    dept = "dept-1"
    targets = [
        SimpleNamespace(name="Sales Target", department_id=None),
        SimpleNamespace(name="sales target", department_id=dept),
    ]
    chosen = _find_assigned_target(targets, "sales target", dept)
    assert chosen.department_id == dept


def test_find_assigned_target_ignores_inactive_targets():
    targets = [
        SimpleNamespace(name="Sales Target", department_id="dept-1", status="INACTIVE"),
        SimpleNamespace(name="Sales Target", department_id=None, status="ACTIVE"),
    ]
    chosen = _find_assigned_target(targets, "sales target", "dept-1")
    assert chosen.department_id is None


def test_formula_max_score_uses_configured_category_maximums():
    formula = SimpleNamespace(categories=[
        {"name": "A", "weight": 0.5, "max_score": 10},
        {"name": "B", "weight": 0.5, "max_score": 2},
    ])
    assert formula_max_score(formula) == 6


def test_deduction_score_matches_rules_when_category_case_differs():
    rules = [
        SimpleNamespace(
            category_name="late submission",
            rule_name="late submission",
            operator=">=",
            target_value="2",
            deduct_score="1.5",
        ),
        SimpleNamespace(
            category_name="late submission",
            rule_name="late submission",
            operator=">=",
            target_value="5",
            deduct_score="2.5",
        ),
    ]
    score = calculate_deduction_based_score(
        "Late Submission",
        rules,
        {"late submission": 7},
        max_score=5.0,
    )
    assert score == 2.5


async def test_score_formula_categories_matches_standardized_rule_names():
    formula = SimpleNamespace(
        categories=[{"name": "Late Submission", "weight": 1.0, "max_score": 5.0, "evaluation_type": "DEDUCTION"}],
        scoring_rules=[
            SimpleNamespace(
                category_name="late submission",
                rule_name="late submission",
                operator=">=",
                target_value="5",
                deduct_score="2.5",
            )
        ],
    )
    score = await score_formula_categories(
        db=None,
        formula=formula,
        dept_id=None,
        target_month_str="2026-09",
        final_merged_data={"late submission": 7},
    )
    assert score == 2.5
