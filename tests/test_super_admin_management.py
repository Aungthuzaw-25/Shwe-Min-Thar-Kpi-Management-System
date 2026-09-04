from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.users.router import require_super_admin, serialize_user_obj


def test_require_super_admin_allows_super_admin_role():
    user = SimpleNamespace(role=SimpleNamespace(name='Super Admin'))
    assert require_super_admin(user) is None


def test_require_super_admin_rejects_admin_role():
    user = SimpleNamespace(role=SimpleNamespace(name='Admin'))
    with pytest.raises(HTTPException) as exc:
        require_super_admin(user)
    assert exc.value.status_code == 403


def test_serialize_user_obj_excludes_password_and_includes_created_at():
    user = SimpleNamespace(
        id=uuid4(),
        first_name='Alice',
        last_name='Smith',
        email='alice@example.com',
        is_active=True,
        role=SimpleNamespace(id=uuid4(), name='Super Admin'),
        position=None,
        user_departments=[],
        region_town='Yangon',
        created_at='2024-01-01T00:00:00',
    )

    payload = serialize_user_obj(user)
    assert payload.email == 'alice@example.com'
    assert payload.role.name == 'Super Admin'
    assert payload.created_at.isoformat() == '2024-01-01T00:00:00'
    assert 'hashed_password' not in payload.model_dump()
