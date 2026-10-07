from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.kpi.category_service import get_eligible_kpi_categories_for_user
from app.modules.kpi.models import FormulaStatus
from app.modules.kpi.service import _category_name, _sync_formula_categories


class _ScalarResult:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return self._values


class _FakeSession:
    def __init__(self, existing_names):
        self.existing_names = existing_names
        self.added = []
        self.flush_count = 0

    async def execute(self, _statement):
        return _ScalarResult(self.existing_names)

    def add_all(self, values):
        values = list(values)
        self.added.extend(values)
        self.existing_names.update(category.name for category in values)

    async def flush(self):
        self.flush_count += 1


class _EligibilitySession:
    def __init__(self, user, membership, position, formulas):
        self.user = user
        self.membership = membership
        self.position = position
        self.formulas = formulas
        self.execute_count = 0

    async def get(self, model, identifier):
        if model.__name__ == "User":
            return self.user
        if model.__name__ == "Position":
            return self.position
        return None

    async def execute(self, _statement):
        self.execute_count += 1
        if self.execute_count == 1:
            return _EligibilityResult(self.membership)
        return _EligibilityResult(
            [
                formula
                for formula in self.formulas
                if formula.department_id == self.membership.department_id
                and formula.user_level == self.membership.user_level
                and formula.is_active
                and formula.status == FormulaStatus.ACTIVE
                and not formula.is_deleted
            ]
        )


class _EligibilityResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value

    def all(self):
        return self.value


def test_category_name_normalization_supports_schema_and_json_values():
    assert _category_name(SimpleNamespace(name="  Attendance  ")) == "Attendance"
    assert _category_name({"name": "  Quality  "}) == "Quality"
    assert _category_name({"name": ""}) == ""


@pytest.mark.asyncio
async def test_formula_categories_are_upserted_without_duplicates():
    db = _FakeSession({"Attendance"})

    await _sync_formula_categories(
        db,
        [
            {"name": "Attendance", "deductions": []},
            {"name": "Quality", "deductions": []},
            {"name": " Quality ", "deductions": []},
            {"name": "Performance", "deductions": []},
        ],
    )

    await _sync_formula_categories(
        db,
        [{"name": "Quality", "deductions": []}, {"name": "Performance", "deductions": []}],
    )

    assert [category.name for category in db.added] == ["Performance", "Quality"]
    assert db.flush_count == 1


@pytest.mark.asyncio
async def test_kpi_categories_are_available_for_matching_employee():
    user_id = uuid4()
    department_id = uuid4()
    position_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        is_active=True,
        is_deleted=False,
        position_id=position_id,
    )
    membership = SimpleNamespace(
        department_id=department_id,
        user_level="L2",
        is_deleted=False,
    )
    position = SimpleNamespace(name="Officer", is_active=True, is_deleted=False)
    formula = SimpleNamespace(
        department_id=department_id,
        user_level="L2",
        position_id=position_id,
        position_name=None,
        categories=[{"name": "Quality"}],
        is_active=True,
        status=FormulaStatus.ACTIVE,
        is_deleted=False,
    )

    categories = await get_eligible_kpi_categories_for_user(
        _EligibilitySession(user, membership, position, [formula]), user_id
    )

    assert categories == {"Quality"}


@pytest.mark.asyncio
async def test_kpi_categories_are_not_available_for_wrong_level():
    user_id = uuid4()
    department_id = uuid4()
    position_id = uuid4()
    user = SimpleNamespace(
        id=user_id,
        is_active=True,
        is_deleted=False,
        position_id=position_id,
    )
    membership = SimpleNamespace(
        department_id=department_id,
        user_level="L1",
        is_deleted=False,
    )
    position = SimpleNamespace(name="Officer", is_active=True, is_deleted=False)
    formula = SimpleNamespace(
        department_id=department_id,
        user_level="L2",
        position_id=position_id,
        position_name=None,
        categories=[{"name": "Quality"}],
        is_active=True,
        status=FormulaStatus.ACTIVE,
        is_deleted=False,
    )

    categories = await get_eligible_kpi_categories_for_user(
        _EligibilitySession(user, membership, position, [formula]), user_id
    )

    assert categories == set()
