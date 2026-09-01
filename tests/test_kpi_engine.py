from types import SimpleNamespace

from app.modules.kpi.engine import KPICalculationEngine
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
