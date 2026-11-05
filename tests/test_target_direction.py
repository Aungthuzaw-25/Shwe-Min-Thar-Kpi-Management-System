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
    """A stored direction outside HIGHER/LOWER counts as absent,
    so a stored-name match with garbage direction stays None —
    the engine treats garbage the same way (legacy inference),
    so no score changes on re-save."""
    incoming = [KPICategoryConfig(
        name="Sales", weight=0.5, evaluation_type="TARGET",
    )]
    stored = [{"name": "Sales", "evaluation_type": "TARGET",
               "direction": "SIDEWAYS"}]
    _resolve_category_directions(incoming, stored)
    assert incoming[0].direction is None


def test_update_merge_keeps_none_for_stored_category_without_direction():
    """Strict rule (Zenn, 2026-10-31): a legacy category whose
    stored row has NO direction stays direction-less through a
    builder re-save — the displayed inference is NOT stamped."""
    incoming = [KPICategoryConfig(
        name="Sales", weight=0.5, evaluation_type="TARGET",
    )]
    stored = [{"name": "Sales", "weight": 0.5,
               "evaluation_type": "TARGET"}]
    _resolve_category_directions(incoming, stored)
    assert incoming[0].direction is None


# --- Update-path tests: direction stays None after a real save ---

import asyncio

from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import BaseModel
from app.modules.kpi.models import FormulaStatus, KPIFormula, KpiCategory
from app.modules.kpi.schemas import (
    KPIFormulaUpdate,
    TIMELINESS_CATEGORY_NAME,
)
from app.modules.kpi.service import update_kpi_formula


def _run(coro_factory, tmp_path):
    db_file = tmp_path / "targetdir.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    BaseModel.metadata.create_all(sync_engine)
    sync_engine.dispose()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_file}", poolclass=None
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _main():
        async with factory() as db:
            return await coro_factory(db)

    try:
        return asyncio.run(_main())
    finally:
        asyncio.run(engine.dispose())


async def _seed_formula(db, status, effective_month=None):
    # Pre-seed lookup rows with explicit ids: KpiCategory.id has a
    # PG-only default, so _sync must find these, not insert them.
    for name in ("Sales", TIMELINESS_CATEGORY_NAME):
        db.add(KpiCategory(id=uuid4(), name=name))
    formula = KPIFormula(
        name="Legacy formula",
        department_id=uuid4(),
        position_name="Tester",
        user_level="L2",
        categories=[
            {"name": "Sales", "weight": 0.99,
             "evaluation_type": "TARGET"},
            {"name": TIMELINESS_CATEGORY_NAME, "weight": 0.01,
             "evaluation_type": "DEDUCTION"},
        ],
        status=status,
        version=1,
        effective_month=effective_month,
    )
    db.add(formula)
    await db.commit()
    return formula.id


def _incoming_update():
    return KPIFormulaUpdate(categories=[
        {"name": "Sales", "weight": 0.99, "evaluation_type": "TARGET"},
        {"name": TIMELINESS_CATEGORY_NAME, "weight": 0.01,
         "evaluation_type": "DEDUCTION"},
    ])


def _sales_direction(categories):
    sales = next(cat for cat in categories if cat["name"] == "Sales")
    return sales.get("direction")


def test_update_draft_path_keeps_legacy_direction_none(tmp_path):
    async def _case(db):
        formula_id = await _seed_formula(db, FormulaStatus.DRAFT)
        updated = await update_kpi_formula(db, formula_id, _incoming_update())
        assert _sales_direction(updated.categories) is None

    _run(_case, tmp_path)


def test_update_version_path_keeps_legacy_direction_none(tmp_path):
    async def _case(db):
        # The version path inherits the old version's month when
        # the update carries no period fields; without any month
        # or period link, main's "no periods and no effective
        # month" guard 422s before the direction assertion.
        formula_id = await _seed_formula(
            db, FormulaStatus.ACTIVE, effective_month="2026-01"
        )
        new_version = await update_kpi_formula(
            db, formula_id, _incoming_update()
        )
        assert new_version.status == FormulaStatus.DRAFT
        assert _sales_direction(new_version.categories) is None

    _run(_case, tmp_path)
