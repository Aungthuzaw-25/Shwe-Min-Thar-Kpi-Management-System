from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from app.core.password_policy import get_password_policy, validate_password_strength
from app.modules.system.router import _sanitize_setting_value
from app.modules.system.schemas import PasswordPolicyValue, SystemSettingResponse


class FakeResult:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return self

    def first(self):
        return self.value


class FakeDB:
    def __init__(self, setting):
        self.setting = setting

    async def execute(self, _statement):
        return FakeResult(self.setting)


def test_security_setting_is_normalized_for_existing_json_storage():
    value = _sanitize_setting_value(
        "security",
        {
            "min_length": 10,
            "require_complexity": True,
            "password_expiry": True,
            "session_timeout": 120,
        },
    )

    assert value["minimum_password_length"] == 10
    assert value["min_length"] == 10
    assert value["force_password_change_days"] == 90
    assert value["password_expiry"] is True


def test_new_setting_response_allows_null_updated_at():
    response = SystemSettingResponse.model_validate(
        {
            "id": uuid4(),
            "key": "security",
            "value": {"minimum_password_length": 10},
            "description": None,
            "created_at": datetime.now(UTC),
            "updated_at": None,
        }
    )
    assert response.updated_at is None


def test_security_setting_rejects_invalid_types_and_ranges():
    with pytest.raises(ValidationError):
        PasswordPolicyValue(
            minimum_password_length="10",  # noqa: S106 - synthetic test fixture; not a production credential
            require_complexity=True,
            force_password_change_days=90,
        )
    with pytest.raises(HTTPException):
        _sanitize_setting_value(
            "security",
            {
                "minimum_password_length": 4,
                "require_complexity": True,
                "force_password_change_days": 90,
            },
        )


@pytest.mark.asyncio
async def test_saved_policy_is_used_by_password_validation():
    setting = SimpleNamespace(
        value={
            "minimum_password_length": 10,
            "require_complexity": False,
            "force_password_change_days": 90,
        }
    )
    policy = await get_password_policy(FakeDB(setting))
    assert policy == {"minimum_length": 10, "require_complexity": False}

    with pytest.raises(HTTPException):
        validate_password_strength("short", **policy)
    assert validate_password_strength("long-enough", **policy) is True
