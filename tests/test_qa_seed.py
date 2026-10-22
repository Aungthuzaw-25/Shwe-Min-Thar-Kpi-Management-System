from datetime import date

from app.database import qa_seed
from app.modules.kpi.models import PeriodType
from app.modules.kpi.period_ranges import calendar_period_range


def test_qa_seed_requires_qa_environment(monkeypatch):
    monkeypatch.setattr(qa_seed.settings, "ENV", "development")
    try:
        qa_seed._require_qa_target()
    except RuntimeError as exc:
        assert "ENV=qa" in str(exc)
    else:
        raise AssertionError("QA seed accepted a non-QA environment")


def test_qa_seed_rejects_remote_database(monkeypatch):
    monkeypatch.setattr(qa_seed.settings, "ENV", "qa")
    monkeypatch.setattr(qa_seed.settings, "DATABASE_URL", "postgresql+asyncpg://user:pass@remote.example/kpi_qa")
    try:
        qa_seed._require_qa_target()
    except RuntimeError as exc:
        assert "postgres-qa" in str(exc)
    else:
        raise AssertionError("QA seed accepted a remote database")


def test_qa_fixture_weights_include_timeliness():
    assert sum(weight for _, weight in qa_seed.QA_FORMULA_CATEGORIES) == 1.0
    assert qa_seed.TIMELINESS in dict(qa_seed.QA_FORMULA_CATEGORIES)


def test_qa_seed_period_uses_canonical_month_boundaries():
    expected = calendar_period_range(PeriodType.MONTHLY, date(2026, 9, 1))

    assert qa_seed.QA_PERIOD_START == expected.start_inclusive
    assert qa_seed.QA_PERIOD_END == expected.end_exclusive
