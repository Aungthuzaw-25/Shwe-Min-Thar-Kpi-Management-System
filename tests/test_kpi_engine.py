from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects import postgresql

from app.core.timezone import APPLICATION_TIMEZONE
from app.modules.departments.models import Department  # noqa: F401 - register string-based relationships in isolated tests
from app.modules.kpi.engine import (
    KPICalculationEngine,
    _find_fallback_formula,
    _collect_deductions,
    formula_max_score,
)
from app.modules.kpi.engine_helpers import (
    _find_assigned_target,
    calculate_target_based_score,
    calculate_deduction_based_score,
    score_formula_categories,
    standardize_key,
)
from app.modules.kpi.models import KPIStatus, PeriodType


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


def test_find_assigned_target_ignores_deleted_target_and_uses_global_fallback():
    targets = [
        SimpleNamespace(
            name="Sales Target",
            department_id="dept-1",
            status="ACTIVE",
            is_deleted=True,
        ),
        SimpleNamespace(
            name="Sales Target",
            department_id=None,
            status="ACTIVE",
            is_deleted=False,
        ),
    ]

    chosen = _find_assigned_target(targets, "sales target", "dept-1")

    assert chosen.department_id is None


def test_formula_max_score_uses_configured_category_maximums():
    formula = SimpleNamespace(categories=[
        {"name": "A", "weight": 0.5, "max_score": 10},
        {"name": "B", "weight": 0.5, "max_score": 2},
    ])
    assert formula_max_score(formula) == 6


@pytest.mark.parametrize(
    ("target_value", "actual_value", "expected_score"),
    [(2.0, 1.0, 5.0), (1.0, 1.0, 5.0), (0.5, 1.0, 2.5)],
)
@pytest.mark.asyncio
async def test_count_target_current_implementation_contract(
    target_value, actual_value, expected_score
):
    target = SimpleNamespace(
        name="Target Count",
        department_id="dept-1",
        target_value=target_value,
        metric_type="COUNT",
        status="ACTIVE",
        is_deleted=False,
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [target])
            )

    score = await calculate_target_based_score(
        Session(),
        "dept-1",
        "2026-11",
        "target count",
        5.0,
        {"target count": actual_value},
    )

    assert score == expected_score


@pytest.mark.parametrize(
    ("targets", "actual_data", "expected_score"),
    [
        ([], {"target count": 1.0}, 0.0),
        ([SimpleNamespace(name="Target Count", department_id="dept-1", target_value=0.0,
                          metric_type="COUNT", status="ACTIVE", is_deleted=False)],
         {"target count": 1.0}, 0.0),
        ([SimpleNamespace(name="Target Count", department_id="dept-1", target_value=-1.0,
                          metric_type="COUNT", status="ACTIVE", is_deleted=False)],
         {"target count": 1.0}, 0.0),
        ([SimpleNamespace(name="Target Count", department_id="dept-1", target_value=2.0,
                          metric_type="COUNT", status="ACTIVE", is_deleted=False)],
         {"target count": 0.0}, 5.0),
        ([SimpleNamespace(name="Target Count", department_id="dept-1", target_value=2.0,
                          metric_type="COUNT", status="ACTIVE", is_deleted=False)],
         {}, 0.0),
        ([SimpleNamespace(name="Target Count", department_id="dept-1", target_value=2.0,
                          metric_type="COUNT", status="ACTIVE", is_deleted=False)],
         {"target count": 3.0}, 10.0 / 3.0),
    ],
)
@pytest.mark.asyncio
async def test_count_target_edge_behavior(targets, actual_data, expected_score):
    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: targets)
            )

    score = await calculate_target_based_score(
        Session(), "dept-1", "2026-11", "target count", 5.0, actual_data
    )

    assert score == pytest.approx(expected_score)


@pytest.mark.asyncio
async def test_category_weight_interpretation_does_not_normalize_total_weight():
    categories = [
        {"name": "Fraction", "weight": 0.3, "max_score": 5.0},
        {"name": "Percent", "weight": 30.0, "max_score": 5.0},
    ]
    formula = SimpleNamespace(categories=categories, scoring_rules=[])

    assert formula_max_score(formula) == pytest.approx(3.0)
    assert await score_formula_categories(None, formula, None, "2026-11", {}) == pytest.approx(3.0)


@pytest.mark.asyncio
async def test_target_lookup_filters_month_and_department_and_allows_global_targets():
    class Session:
        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    session = Session()
    assert await calculate_target_based_score(
        session, "dept-1", "2026-11", "target count", 5.0, {"target count": 1.0}
    ) == 0.0

    compiled = session.statement.compile(
        dialect=postgresql.dialect(),
        compile_kwargs={"literal_binds": True},
    )
    assert "targets.target_month = '2026-11'" in str(compiled)
    assert "targets.department_id = 'dept-1'" in str(compiled)
    assert "targets.department_id IS NULL" in str(compiled)
    assert "targets.is_deleted = false" in str(compiled)


@pytest.mark.asyncio
async def test_target_direction_lower_overrides_non_count_metric():
    """Explicit LOWER beats the legacy inference: a PERCENTAGE
    target would infer HIGHER (50/100 -> 2.5), but direction LOWER
    scores target/actual (100/50 -> clamp -> 5.0)."""
    target = SimpleNamespace(
        name="Defect Rate", department_id="dept-1", target_value=100.0,
        metric_type="PERCENTAGE", status="ACTIVE", is_deleted=False,
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [target])
            )

    assert await calculate_target_based_score(
        Session(), "dept-1", "2026-11", "defect rate", 5.0,
        {"defect rate": 50.0}, direction="LOWER",
    ) == 5.0
    # No direction: legacy HIGHER inference for non-COUNT.
    assert await calculate_target_based_score(
        Session(), "dept-1", "2026-11", "defect rate", 5.0,
        {"defect rate": 50.0},
    ) == 2.5


@pytest.mark.asyncio
async def test_target_direction_higher_overrides_count_metric():
    """Explicit HIGHER beats the legacy COUNT inference: COUNT
    would infer LOWER (2/1 -> clamp -> 5.0), but direction HIGHER
    scores actual/target (1/2 -> 2.5)."""
    target = SimpleNamespace(
        name="Target Count", department_id="dept-1", target_value=2.0,
        metric_type="COUNT", status="ACTIVE", is_deleted=False,
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [target])
            )

    assert await calculate_target_based_score(
        Session(), "dept-1", "2026-11", "target count", 5.0,
        {"target count": 1.0}, direction="HIGHER",
    ) == 2.5
    # No direction: legacy LOWER inference for COUNT (already
    # covered by the contract tests above; pinned here too).
    assert await calculate_target_based_score(
        Session(), "dept-1", "2026-11", "target count", 5.0,
        {"target count": 1.0},
    ) == 5.0


@pytest.mark.asyncio
async def test_score_formula_categories_passes_category_direction():
    """The category dict's direction reaches the engine, and a
    category without one keeps the legacy inference."""
    target = SimpleNamespace(
        name="Defect Rate", department_id="dept-1", target_value=100.0,
        metric_type="PERCENTAGE", status="ACTIVE", is_deleted=False,
    )

    class Session:
        async def execute(self, _statement):
            return SimpleNamespace(
                scalars=lambda: SimpleNamespace(all=lambda: [target])
            )

    formula = SimpleNamespace(
        categories=[{
            "name": "Defect Rate", "weight": 1.0, "max_score": 5.0,
            "evaluation_type": "TARGET", "direction": "LOWER",
        }],
        scoring_rules=[],
    )
    assert await score_formula_categories(
        Session(), formula, "dept-1", "2026-11", {"defect rate": 50.0}
    ) == 5.0

    legacy_formula = SimpleNamespace(
        categories=[{
            "name": "Defect Rate", "weight": 1.0, "max_score": 5.0,
            "evaluation_type": "TARGET",
        }],
        scoring_rules=[],
    )
    assert await score_formula_categories(
        Session(), legacy_formula, "dept-1", "2026-11", {"defect rate": 50.0}
    ) == 2.5


@pytest.mark.asyncio
async def test_deduction_category_direction_is_ignored():
    """A direction stored on a DEDUCTION category must not change
    its rule-based score."""
    formula = SimpleNamespace(
        categories=[{
            "name": "Late Submission", "weight": 1.0, "max_score": 5.0,
            "evaluation_type": "DEDUCTION", "direction": "LOWER",
        }],
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
    assert await score_formula_categories(
        db=None, formula=formula, dept_id=None,
        target_month_str="2026-09",
        final_merged_data={"late submission": 7},
    ) == 2.5


def test_deduction_rule_groups_use_maximum_within_group_and_sum_across_groups():
    rules = [
        SimpleNamespace(rule_name="Rule A", operator=">=", target_value="1", deduct_score="1"),
        SimpleNamespace(rule_name="rule a", operator=">=", target_value="2", deduct_score="2"),
        SimpleNamespace(rule_name="Rule B", operator=">=", target_value="1", deduct_score="1.5"),
    ]

    score = KPICalculationEngine.evaluate_db_rules(
        "Category",
        rules,
        {"category": 2},
        max_score=5.0,
    )

    assert score == 1.5


@pytest.mark.parametrize(
    ("membership_level", "expected_level"),
    [("L0", "L0"), ("Level 1", "L1"), ("l2", "L2")],
)
@pytest.mark.asyncio
async def test_fallback_formula_resolution_filters_by_membership_level(
    membership_level, expected_level
):
    department_id = "department-1"
    position_id = "position-1"
    user = SimpleNamespace(
        position_id=position_id,
        position=SimpleNamespace(
            name="Position",
            is_active=True,
            is_deleted=False,
            department_id=department_id,
        ),
        user_departments=[
            SimpleNamespace(
                department_id=department_id,
                user_level=membership_level,
                is_deleted=False,
            )
        ],
        region_town=None,
    )

    class Session:
        async def execute(self, statement):
            self.statement = statement
            return SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))

    session = Session()
    assert await _find_fallback_formula(session, user, "2026-11") is None
    compiled = session.statement.compile(dialect=postgresql.dialect())
    assert expected_level in compiled.params.values()


@pytest.mark.asyncio
async def test_fallback_formula_resolution_requires_an_active_user_level():
    user = SimpleNamespace(
        position_id="position-1",
        position=SimpleNamespace(
            name="Position",
            is_active=True,
            is_deleted=False,
            department_id="department-1",
        ),
        user_departments=[],
        region_town=None,
    )

    class Session:
        async def execute(self, _statement):
            raise AssertionError("Formula query must not run without a user level.")

    assert await _find_fallback_formula(Session(), user, "2026-11") is None


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


@pytest.mark.asyncio
async def test_execute_kpi_calculation_scores_formula_categories(monkeypatch):
    formula = SimpleNamespace(
        id="formula-1",
        department_id="department-1",
        categories=[{
            "name": "Late Submission",
            "weight": 1.0,
            "max_score": 5.0,
            "evaluation_type": "DEDUCTION",
        }],
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
    user = SimpleNamespace(id="user-1")
    db = SimpleNamespace(commit=lambda: None, rollback=lambda: None)
    saved = {}

    async def resolve_formula(_db, _user, _target_date):
        return formula, None

    async def collect_deductions(_db, _user, _formula, _period_start, _period_end):
        return {}, {"Late Submission": [7]}

    async def save_record(
        _db,
        _user,
        _formula,
        _period,
        _period_type,
        _period_start,
        _period_end,
        total_score,
        percentage,
    ):
        saved["total_score"] = total_score
        saved["percentage"] = percentage

    async def commit():
        saved["committed"] = True

    async def rollback():
        saved["rolled_back"] = True

    db.commit = commit
    db.rollback = rollback
    monkeypatch.setattr(
        KPICalculationEngine,
        "get_applicable_formula_and_period",
        resolve_formula,
    )
    monkeypatch.setattr(
        "app.modules.kpi.engine._collect_deductions",
        collect_deductions,
    )
    monkeypatch.setattr(
        "app.modules.kpi.engine._save_kpi_record",
        save_record,
    )

    await KPICalculationEngine.execute_kpi_calculation(
        db=db,
        user=user,
        period_start=datetime(2026, 9, 1),
        period_end=datetime(2026, 9, 30, 23, 59, 59),
        period_type=PeriodType.MONTHLY,
    )

    assert saved == {
        "total_score": 2.5,
        "percentage": 50.0,
        "committed": True,
    }


@pytest.mark.asyncio
async def test_collect_deductions_uses_storage_timezone_for_created_at_bounds():
    start = datetime(2026, 6, 1, tzinfo=APPLICATION_TIMEZONE)
    end = datetime(2026, 7, 1, tzinfo=APPLICATION_TIMEZONE)

    class QueryResult:
        def all(self):
            return []

        def scalar(self):
            return 0

    class ReadOnlyDatabase:
        statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return QueryResult()

    db = ReadOnlyDatabase()
    await _collect_deductions(
        db,
        SimpleNamespace(id="user-1"),
        SimpleNamespace(categories=[]),
        start,
        end,
    )

    compiled = db.statements[0].compile(dialect=postgresql.dialect())
    datetime_bind_values = {
        bind.value
        for bind in compiled.binds.values()
        if isinstance(bind.value, datetime)
    }
    assert start in datetime_bind_values
    assert end in datetime_bind_values
    assert start.astimezone(UTC).replace(tzinfo=None) in datetime_bind_values
    assert end.astimezone(UTC).replace(tzinfo=None) in datetime_bind_values
