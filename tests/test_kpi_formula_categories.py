from types import SimpleNamespace

import pytest

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
