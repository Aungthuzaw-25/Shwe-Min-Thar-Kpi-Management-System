import pytest
from fastapi import HTTPException

from app.modules.kpi.router import _normalize_report_period_type


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("MONTHLY", "MONTHLY"),
        ("monthly", "MONTHLY"),
        ("Monthly", "MONTHLY"),
    ],
)
def test_normalize_report_period_type_accepts_case_insensitive_values(value, expected):
    assert _normalize_report_period_type(value) == expected


def test_normalize_report_period_type_rejects_invalid_value():
    with pytest.raises(HTTPException) as exc_info:
        _normalize_report_period_type("INVALID")

    assert exc_info.value.status_code == 422


def test_normalize_report_period_type_rejects_missing_value():
    with pytest.raises(HTTPException) as exc_info:
        _normalize_report_period_type(None)

    assert exc_info.value.status_code == 422
