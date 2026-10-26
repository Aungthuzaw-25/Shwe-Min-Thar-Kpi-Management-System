"""Target direction selector (Zenn, 2026-10-26): schema validation,
create defaults, and the update merge that protects legacy scores."""

from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.kpi.schemas import (
    KPIFormulaCreate,
    KPICategoryConfig,
    TIMELINESS_CATEGORY_NAME,
)
from app.modules.kpi.service import _resolve_category_directions


def test_direction_rejects_values_outside_higher_lower():
    with pytest.raises(ValidationError):
        KPICategoryConfig(
            name="Sales", weight=0.99, evaluation_type="TARGET",
            direction="SIDEWAYS",
        )


def _create_formula(categories):
    return KPIFormulaCreate(
        name="Direction test formula",
        department_id=uuid4(),
        position_name="Tester",
        user_level="L2",
        categories=categories,
    )


def test_create_defaults_new_target_to_higher():
    formula = _create_formula([
        {"name": "Sales", "weight": 0.99, "evaluation_type": "TARGET"},
        {"name": TIMELINESS_CATEGORY_NAME, "weight": 0.01,
         "evaluation_type": "DEDUCTION"},
    ])
    sales = next(cat for cat in formula.categories if cat.name == "Sales")
    assert sales.direction == "HIGHER"


def test_create_keeps_explicit_lower_and_leaves_deduction_without_direction():
    formula = _create_formula([
        {"name": "Defects", "weight": 0.99, "evaluation_type": "TARGET",
         "direction": "LOWER"},
        {"name": TIMELINESS_CATEGORY_NAME, "weight": 0.01,
         "evaluation_type": "DEDUCTION"},
    ])
    defects = next(cat for cat in formula.categories if cat.name == "Defects")
    timeliness = next(
        cat for cat in formula.categories
        if cat.name == TIMELINESS_CATEGORY_NAME
    )
    assert defects.direction == "LOWER"
    assert timeliness.direction is None


def test_update_merge_preserves_stored_direction():
    """Re-saving a stored LOWER category without direction must not
    flip it to HIGHER or to the legacy inference."""
    incoming = [KPICategoryConfig(
        name="Defects", weight=0.5, evaluation_type="TARGET",
    )]
    stored = [{"name": "Defects", "weight": 0.5,
               "evaluation_type": "TARGET", "direction": "LOWER"}]
    _resolve_category_directions(incoming, stored)
    assert incoming[0].direction == "LOWER"


def test_update_merge_defaults_new_target_and_leaves_new_deduction():
    incoming = [
        KPICategoryConfig(name="Sales", weight=0.5,
                          evaluation_type="TARGET"),
        KPICategoryConfig(name="Late Work", weight=0.5,
                          evaluation_type="DEDUCTION"),
    ]
    _resolve_category_directions(incoming, [{"name": "Other"}])
    assert incoming[0].direction == "HIGHER"
    assert incoming[1].direction is None


def test_update_merge_keeps_explicit_direction_over_stored():
    incoming = [KPICategoryConfig(
        name="Defects", weight=0.5, evaluation_type="TARGET",
        direction="HIGHER",
    )]
    stored = [{"name": "Defects", "evaluation_type": "TARGET",
               "direction": "LOWER"}]
    _resolve_category_directions(incoming, stored)
    assert incoming[0].direction == "HIGHER"


def test_update_merge_treats_garbage_stored_direction_as_absent():
    incoming = [KPICategoryConfig(
        name="Sales", weight=0.5, evaluation_type="TARGET",
    )]
    stored = [{"name": "Sales", "evaluation_type": "TARGET",
               "direction": "SIDEWAYS"}]
    _resolve_category_directions(incoming, stored)
    assert incoming[0].direction == "HIGHER"
