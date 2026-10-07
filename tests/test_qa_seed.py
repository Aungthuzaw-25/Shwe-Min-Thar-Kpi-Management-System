from app.database import qa_seed


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
