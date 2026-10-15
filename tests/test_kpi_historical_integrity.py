from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.kpi.engine import _save_kpi_record
from app.modules.kpi.models import KPIPeriodStatus, PeriodType
from app.modules.kpi.period_service import ensure_kpi_record_editable


@pytest.mark.parametrize(
    "period_status",
    [
        KPIPeriodStatus.FINALIZED,
        KPIPeriodStatus.LOCKED,
        KPIPeriodStatus.CANCELLED,
    ],
)
def test_closed_period_kpi_record_cannot_be_mutated(period_status):
    record = SimpleNamespace(period=SimpleNamespace(status=period_status))

    with pytest.raises(HTTPException) as error:
        ensure_kpi_record_editable(record)

    assert error.value.status_code == 409


@pytest.mark.parametrize(
    "period_status",
    [
        KPIPeriodStatus.DRAFT,
        KPIPeriodStatus.OPEN,
        KPIPeriodStatus.IN_PROGRESS,
        KPIPeriodStatus.REVIEW,
    ],
)
def test_editable_period_kpi_record_remains_mutable(period_status):
    ensure_kpi_record_editable(
        SimpleNamespace(period=SimpleNamespace(status=period_status))
    )


def test_legacy_kpi_record_without_period_remains_editable():
    ensure_kpi_record_editable(SimpleNamespace(period=None))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "period_status",
    [KPIPeriodStatus.FINALIZED, KPIPeriodStatus.LOCKED],
)
async def test_recalculation_rejects_authoritative_period(period_status):
    class Result:
        def scalars(self):
            return self

        def first(self):
            return SimpleNamespace(status=period_status)

    class Database:
        async def execute(self, statement):
            return Result()

    db = Database()
    user = SimpleNamespace(id="user-1")
    formula = SimpleNamespace(id="formula-1")
    period = SimpleNamespace(id="period-1")

    with pytest.raises(ValueError, match="finalized, locked"):
        await _save_kpi_record(
            db,
            user,
            formula,
            period,
            PeriodType.MONTHLY,
            "2026-09-01",
            "2026-09-30",
            4.0,
            80.0,
        )
