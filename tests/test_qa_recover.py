from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.database import qa_recover


class _Scalars:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value

    def all(self):
        return []


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _Scalars(self.value)

    def first(self):
        return self.value


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class _Session:
    def __init__(self, target):
        self.target = target
        self.added = []
        self.executed = []
        self.rollback_count = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    def begin(self):
        return _Transaction()

    async def execute(self, statement, params=None):
        self.executed.append((str(statement), params))
        if "users" in str(statement):
            value = (self.target, self.target.role.name) if self.target else None
            return _Result(value)
        return _Result(None)

    def add(self, row):
        self.added.append(row)

    async def rollback(self):
        self.rollback_count += 1


def _settings(**overrides):
    values = {
        "ENV": "qa",
        "DATABASE_URL": "postgresql+asyncpg://qa:qa@postgres-qa:5432/kpi_qa",
        "QA_DATABASE_NAME": "kpi_qa",
        "QA_DATABASE_HOST": "postgres-qa",
        "QA_DATABASE_PORT": 5432,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _target(*, email="qa.superadmin@example", role="Super Admin", deleted=False):
    return SimpleNamespace(
        id=uuid4(),
        email=email,
        role=SimpleNamespace(name=role, permissions=["kpi.calculate"]),
        role_id=uuid4(),
        position_id=uuid4(),
        hashed_password="old-hash",
        local_password_enabled=True,
        must_change_password=False,
        is_deleted=deleted,
    )


def test_qa_environment_is_accepted():
    assert qa_recover.validate_recovery_target(
        "qa.superadmin@example", _settings()
    ) == ("qa.superadmin@example", "Super Admin")


@pytest.mark.parametrize(
    "configuration",
    [
        _settings(ENV="production"),
        _settings(DATABASE_URL="postgresql+asyncpg://qa:qa@postgres-qa:5432/other"),
        _settings(DATABASE_URL="postgresql+asyncpg://qa:qa@remote:5432/kpi_qa"),
    ],
)
def test_non_qa_or_wrong_database_is_rejected(configuration):
    with pytest.raises(RuntimeError):
        qa_recover.validate_recovery_target(
            "qa.superadmin@example", configuration
        )


def test_non_allowlisted_email_is_rejected():
    configuration = _settings()
    with pytest.raises(
        qa_recover.RecoveryFailure, match="TARGET_VALIDATION_FAILED"
    ):
        qa_recover.validate_recovery_target("someone@example.test", configuration)


@pytest.mark.asyncio
async def test_missing_target_account_is_rejected(monkeypatch):
    db = _Session(None)
    configuration = _settings()
    session_factory = lambda: db
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    with pytest.raises(qa_recover.RecoveryFailure, match="TARGET_ACCOUNT_NOT_FOUND"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_deleted_target_account_is_rejected(monkeypatch):
    db = _Session(_target(deleted=True))
    configuration = _settings()
    session_factory = lambda: db
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    with pytest.raises(qa_recover.RecoveryFailure, match="TARGET_VALIDATION_FAILED"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_wrong_role_is_rejected(monkeypatch):
    db = _Session(_target(role="Admin"))
    configuration = _settings()
    session_factory = lambda: db
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    with pytest.raises(qa_recover.RecoveryFailure, match="ROLE_VALIDATION_FAILED"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_weak_password_is_rejected_before_mutation(monkeypatch):
    target = _target()
    db = _Session(target)
    configuration = _settings()
    session_factory = lambda: db
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    with pytest.raises(qa_recover.RecoveryFailure, match="PASSWORD_POLICY_FAILED"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "weak",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )
    assert target.hashed_password == "old-hash"
    assert db.added == []


@pytest.mark.asyncio
async def test_missing_confirmation_is_rejected_before_session_open():
    def session_factory():
        raise AssertionError("Database session opened before confirmation.")

    configuration = _settings()
    with pytest.raises(qa_recover.RecoveryFailure, match="CONFIRMATION_FAILED"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            None,
            session_factory=session_factory,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_valid_qa_reset_changes_only_target_password_state(monkeypatch, caplog):
    target = _target()
    db = _Session(target)
    unrelated = _target(email="qa.l2@example.test", role="Department User")
    old_target_state = {
        "role_id": target.role_id,
        "role_permissions": tuple(target.role.permissions),
        "position_id": target.position_id,
        "email": target.email,
        "unrelated_role_id": unrelated.role_id,
        "unrelated_hash": unrelated.hashed_password,
    }
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    monkeypatch.setattr(qa_recover.password_repo, "check_password_history", _history_ok)
    monkeypatch.setattr(qa_recover.password_repo, "add_password_history", _add_history)
    monkeypatch.setattr(qa_recover, "get_password_hash", lambda password: "new-test-hash")

    await qa_recover.recover_qa_password(
        "qa.superadmin@example",
        "StrongPassword123!",
        qa_recover.CONFIRMATION_TEXT,
        session_factory=lambda: db,
        configuration=_settings(),
    )

    assert target.hashed_password == "new-test-hash"
    assert target.local_password_enabled is True
    assert target.must_change_password is True
    assert target.role_id == old_target_state["role_id"]
    assert tuple(target.role.permissions) == old_target_state["role_permissions"]
    assert target.position_id == old_target_state["position_id"]
    assert target.email == old_target_state["email"]
    assert unrelated.role_id == old_target_state["unrelated_role_id"]
    assert unrelated.hashed_password == old_target_state["unrelated_hash"]
    assert len(db.added) == 1
    assert db.added[0].user_id == target.id
    assert all("kpi_" not in sql for sql, _ in db.executed)
    assert all(params == {"user_id": str(target.id)} for _, params in db.executed if params)
    assert all("StrongPassword123!" not in item for item, _ in db.executed)
    assert "StrongPassword123!" not in caplog.text
    assert "new-test-hash" not in caplog.text


@pytest.mark.asyncio
async def test_operator_confirmation_is_requested_after_account_validation(monkeypatch):
    target = _target()
    db = _Session(target)
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    monkeypatch.setattr(qa_recover.password_repo, "check_password_history", _history_ok)
    monkeypatch.setattr(qa_recover.password_repo, "add_password_history", _add_history)
    monkeypatch.setattr(qa_recover, "get_password_hash", lambda _password: "new-test-hash")

    def confirm():
        assert len(db.executed) == 1
        assert target.hashed_password == "old-hash"
        return qa_recover.CONFIRMATION_TEXT

    await qa_recover.recover_qa_password(
        "qa.superadmin@example",
        "StrongPassword123!",
        None,
        confirm=confirm,
        session_factory=lambda: db,
        configuration=_settings(),
    )

    assert target.hashed_password == "new-test-hash"


@pytest.mark.asyncio
async def test_wrong_role_name_on_loaded_target_is_rejected(monkeypatch):
    target = _target()
    target.role.name = "Admin"
    db = _Session(target)
    configuration = _settings()
    session_factory = lambda: db
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    with pytest.raises(qa_recover.RecoveryFailure, match="ROLE_VALIDATION_FAILED"):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


@pytest.mark.asyncio
async def test_target_query_reads_role_without_lazy_relationship(monkeypatch):
    target = _target()
    db = _Session(target)
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)
    monkeypatch.setattr(qa_recover.password_repo, "check_password_history", _history_ok)
    monkeypatch.setattr(qa_recover.password_repo, "add_password_history", _add_history)
    monkeypatch.setattr(qa_recover, "get_password_hash", lambda _password: "new-test-hash")
    configuration = _settings()
    session_factory = lambda: db

    await qa_recover.recover_qa_password(
        "qa.superadmin@example",
        "StrongPassword123!",
        qa_recover.CONFIRMATION_TEXT,
        session_factory=session_factory,
        configuration=configuration,
    )

    lookup_sql = db.executed[0][0]
    assert "JOIN roles" in lookup_sql
    assert "roles.name" in lookup_sql


@pytest.mark.asyncio
async def test_password_history_failure_is_categorized(monkeypatch):
    db = _Session(_target())
    monkeypatch.setattr(qa_recover, "get_password_policy", _password_policy)

    async def fail_history(*_args):
        raise RuntimeError("sensitive password/hash data must not be surfaced")

    monkeypatch.setattr(qa_recover.password_repo, "check_password_history", fail_history)
    configuration = _settings()
    session_factory = lambda: db
    with pytest.raises(
        qa_recover.RecoveryFailure, match="PASSWORD_HISTORY_CHECK_FAILED"
    ):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


def test_unexpected_cli_failure_is_safely_categorized(monkeypatch, caplog):
    async def fail_recovery(_email):
        raise ValueError("plaintext password and password hash")

    monkeypatch.setattr(qa_recover, "_run_interactive", fail_recovery)
    monkeypatch.setattr("sys.argv", ["qa_recover", "--email", "qa.superadmin@example"])

    assert qa_recover.main() == 1
    assert "QA_RECOVERY_ERROR=UNEXPECTED_RECOVERY_ERROR" in caplog.text
    assert "ValueError" in caplog.text
    assert "plaintext password" not in caplog.text
    assert "password hash" not in caplog.text


@pytest.mark.asyncio
async def test_unexpected_recovery_exception_is_safely_categorized(monkeypatch):
    class _BrokenSessionFactory:
        def __call__(self):
            raise ValueError("password and hash must remain private")

    session_factory = _BrokenSessionFactory()
    configuration = _settings()
    with pytest.raises(
        qa_recover.RecoveryFailure, match="UNEXPECTED_RECOVERY_ERROR"
    ):
        await qa_recover.recover_qa_password(
            "qa.superadmin@example",
            "StrongPassword123!",
            qa_recover.CONFIRMATION_TEXT,
            session_factory=session_factory,
            configuration=configuration,
        )


async def _password_policy(_db):
    return {"minimum_length": 12, "require_complexity": True}


async def _history_ok(_db, _user_id, _password):
    return True


async def _add_history(db, user_id, hashed_password, *, commit):
    from app.modules.auth.models import PasswordHistory

    assert commit is False
    db.add(PasswordHistory(user_id=user_id, hashed_password=hashed_password))
