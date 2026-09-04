from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.departments import repo
from app.modules.departments.models import Department
from app.modules.departments.router import create_department, update_department
from app.modules.departments.schemas import DepartmentCreate, DepartmentResponse, DepartmentUpdate


class FakeDB:
    async def commit(self):
        return None

    async def refresh(self, _entity):
        return None

    async def rollback(self):
        return None


def admin_user():
    return SimpleNamespace(
        role=SimpleNamespace(name="Super Admin", permissions=[]),
        permission_overrides=[],
    )


@pytest.mark.asyncio
async def test_create_department_passes_priority_to_persistence(monkeypatch):
    persisted = {}

    async def fake_get_by_name(_db, _name):
        return None

    async def fake_create(_db, payload):
        persisted.update(payload)
        return SimpleNamespace(
            id=uuid4(),
            name=payload["name"],
            priority=payload["priority"],
            head_id=None,
        )

    monkeypatch.setattr(repo, "get_department_by_name", fake_get_by_name)
    monkeypatch.setattr(repo, "create_department", fake_create)

    response = await create_department(
        DepartmentCreate(name="Finance & Account", priority=1),
        FakeDB(),
        admin_user(),
    )

    assert persisted == {"name": "Finance & Account", "priority": 1}
    assert response.priority == 1


@pytest.mark.asyncio
async def test_update_department_persists_priority_zero_and_positive_values(monkeypatch):
    department = SimpleNamespace(
        id=uuid4(),
        name="Finance & Account",
        priority=1,
        head_id=None,
    )

    async def fake_get_by_id(_db, _department_id):
        return department

    async def fake_update(_db, entity, payload):
        for key, value in payload.items():
            setattr(entity, key, value)
        return entity

    monkeypatch.setattr(repo, "get_department_by_id", fake_get_by_id)
    monkeypatch.setattr(repo, "update_department", fake_update)

    response = await update_department(
        department.id,
        DepartmentUpdate(priority=5),
        FakeDB(),
        admin_user(),
    )

    assert response.priority == 5

    response = await update_department(
        department.id,
        DepartmentUpdate(priority=0),
        FakeDB(),
        admin_user(),
    )

    assert response.priority == 0


def test_department_priority_is_persistent_model_data_and_response_data():
    department = Department(id=uuid4(), name="Human Resources", priority=3)

    response = DepartmentResponse.model_validate(department)

    assert department.priority == 3
    assert response.priority == 3
