from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest
from fastapi import HTTPException

from app.core.dependencies import enforce_department_scope
from app.modules.kpi import assignment_service, upload_router
from app.modules.kpi import repo as kpi_repo
from app.modules.kpi import router as kpi_router
from app.modules.kpi.models import FormulaStatus, PeriodType
from app.modules.kpi.schemas import ExplicitKpiCalculationRequest
from app.modules.users import router as users_router


class _Scalars:
    def __init__(self, values):
        self._values = values

    def all(self):
        return list(self._values)


class _Result:
    def __init__(self, values):
        self._values = values

    def scalars(self):
        return _Scalars(self._values)

    def all(self):
        return list(self._values)


class _Session:
    def __init__(self, values=()):
        self.values = values
        self.commits = 0
        self.refreshes = 0

    async def execute(self, _statement):
        return _Result(self.values)

    async def commit(self):
        self.commits += 1

    async def refresh(self, _value):
        self.refreshes += 1


def _user(role_name, user_id=None):
    return SimpleNamespace(id=user_id or uuid4(), role=SimpleNamespace(name=role_name))


@pytest.mark.asyncio
async def test_department_scope_denies_admin_outside_delegation_and_keeps_super_admin_global():
    department_a, department_b = uuid4(), uuid4()
    admin = _user("Admin")
    session = _Session([department_a])

    await enforce_department_scope(session, admin, department_a)
    with pytest.raises(HTTPException) as denied:
        await enforce_department_scope(session, admin, department_b)
    assert denied.value.status_code == 403

    # Global access must not depend on a department membership query.
    await enforce_department_scope(None, _user("Super Admin"), department_b)


@pytest.mark.asyncio
async def test_formula_create_and_update_use_authoritative_department_scope(monkeypatch):
    department_a, department_b, formula_id = uuid4(), uuid4(), uuid4()
    admin = _user("Admin")
    calls = []
    created = []
    updated = []

    async def scope(_db, _user, department_id):
        calls.append(department_id)
        if _user.role.name == "Admin" and department_id == department_b:
            raise HTTPException(status_code=403, detail="Access denied")

    async def create(_db, payload):
        created.append(payload)
        return payload

    async def load(_db, _user, requested_formula_id):
        assert requested_formula_id == formula_id
        await scope(_db, _user, department_b)

    async def update(_db, _formula_id, payload):
        updated.append(payload)
        return payload

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(kpi_router, "enforce_department_scope", scope)
    monkeypatch.setattr(kpi_router.kpi_service, "create_kpi_formula", create)
    monkeypatch.setattr(kpi_router, "_load_formula_in_scope", load)
    monkeypatch.setattr(kpi_router.kpi_service, "update_kpi_formula", update)

    payload = SimpleNamespace(department_id=department_a)
    db = _Session()
    assert await kpi_router.create_kpi_formula(payload, db, admin) is payload
    assert created == [payload]

    payload.department_id = department_b
    denied_db = _Session()
    with pytest.raises(HTTPException) as denied_create:
        await kpi_router.create_kpi_formula(payload, denied_db, admin)
    assert denied_create.value.status_code == 403
    assert created == [payload]  # The denied call did not create a second formula.

    update_payload = SimpleNamespace()
    update_db = _Session()
    with pytest.raises(HTTPException) as denied_update:
        await kpi_router.edit_kpi_formula(formula_id, update_payload, update_db, admin)
    assert denied_update.value.status_code == 403
    assert updated == []
    assert calls == [department_a, department_b, department_b]

    super_admin = _user("Super Admin")
    super_payload = SimpleNamespace(department_id=department_b)
    assert await kpi_router.create_kpi_formula(super_payload, _Session(), super_admin) is super_payload
    assert await kpi_router.edit_kpi_formula(formula_id, super_payload, _Session(), super_admin) is super_payload
    assert created[-1] is super_payload
    assert updated == [super_payload]


@pytest.mark.asyncio
async def test_formula_list_isolates_admin_and_preserves_super_admin_global_access(monkeypatch):
    department_a, department_b = uuid4(), uuid4()
    formula_a = SimpleNamespace(department_id=department_a)
    formula_b = SimpleNamespace(department_id=department_b)

    async def formulas(*_args, **_kwargs):
        return [formula_a, formula_b]

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(kpi_router.kpi_repo, "get_all_formulas", formulas)
    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", lambda *_: _async_value({str(department_a)}))
    assert await kpi_router.get_kpi_formulas(0, 100, _Session(), _user("Admin")) == [formula_a]

    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", lambda *_: _async_value(None))
    assert await kpi_router.get_kpi_formulas(0, 100, _Session(), _user("Super Admin")) == [formula_a, formula_b]


async def _async_value(value):
    return value


@pytest.mark.asyncio
async def test_explicit_calculation_role_and_permission_denials_have_no_side_effects(
    monkeypatch,
):
    class Database:
        def __init__(self, record):
            self.record = record
            self.get_calls = 0

        async def get(self, _model, _record_id):
            self.get_calls += 1
            return self.record

    record_id, assignment_id, period_id, formula_id = (
        uuid4(),
        uuid4(),
        uuid4(),
        uuid4(),
    )
    calculation_calls = []

    async def calculate(_db, *, context):
        calculation_calls.append(context)

    monkeypatch.setattr(
        kpi_router.KPICalculationEngine,
        "execute_kpi_calculation",
        calculate,
    )
    payload = ExplicitKpiCalculationRequest(
        record_id=record_id,
        assignment_id=assignment_id,
        period_id=period_id,
        formula_id=formula_id,
        user_id=None,
    )

    admin_without_permission = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(name="Admin", permissions=[]),
        permission_overrides=[],
    )
    denied_admin_db = Database(
        SimpleNamespace(id=record_id, is_deleted=False, user_id=admin_without_permission.id)
    )
    # E2: the deleted allow_admins role helper is gone; the endpoint's
    # own kpi.calculate permission gate denies before any side effect.
    with pytest.raises(HTTPException) as admin_denied:
        await kpi_router.calculate_kpi_record_explicitly(
            payload, denied_admin_db, admin_without_permission
        )
    assert admin_denied.value.status_code == 403
    assert denied_admin_db.get_calls == 0
    assert calculation_calls == []

    department_user = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(
            name="Department User",
            permissions=[SimpleNamespace(code="kpi.calculate")],
        ),
        permission_overrides=[],
    )
    department_db = Database(
        SimpleNamespace(id=record_id, is_deleted=False, user_id=department_user.id)
    )
    # E2 + D1: a Department User holding kpi.calculate is NO LONGER
    # blocked by the old SA+Admin role gate — the catalog key is the
    # gate now, so the calculation runs (self-scope passes).
    response = await kpi_router.calculate_kpi_record_explicitly(
        payload, department_db, department_user
    )
    assert response["record_id"] == str(record_id)
    assert department_db.get_calls == 1
    assert len(calculation_calls) == 1

    admin_with_permission = SimpleNamespace(
        id=uuid4(),
        role=SimpleNamespace(
            name="Admin",
            permissions=[SimpleNamespace(code="kpi.calculate")],
        ),
        permission_overrides=[],
    )
    authorized_db = Database(
        SimpleNamespace(id=record_id, is_deleted=False, user_id=admin_with_permission.id)
    )
    response = await kpi_router.calculate_kpi_record_explicitly(
        payload, authorized_db, admin_with_permission
    )

    assert response["record_id"] == str(record_id)
    assert authorized_db.get_calls == 1
    assert len(calculation_calls) == 2


@pytest.mark.asyncio
async def test_toggle_and_state_transition_reject_cross_department_without_mutation(monkeypatch):
    formula_id = uuid4()
    formula = SimpleNamespace(is_active=False, status=FormulaStatus.DRAFT)
    session = _Session()
    toggles = []

    async def denied(*_args):
        raise HTTPException(status_code=403, detail="Access denied")

    async def toggle(_db, _formula, _is_active):
        toggles.append(True)

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(kpi_router, "_load_formula_in_scope", denied)
    monkeypatch.setattr(kpi_router.kpi_repo, "toggle_formula_status", toggle)
    toggle_payload = SimpleNamespace(is_active=True)
    admin = _user("Admin")

    with pytest.raises(HTTPException) as denied_toggle:
        await kpi_router.toggle_kpi_formula(formula_id, toggle_payload, session, admin)
    assert denied_toggle.value.status_code == 403
    assert formula.is_active is False
    assert toggles == []

    transition_user = _user("Admin")
    with pytest.raises(HTTPException) as denied_transition:
        await kpi_router._transition_formula_state(
            session, formula_id, [FormulaStatus.DRAFT], FormulaStatus.REVIEW, transition_user
        )
    assert denied_transition.value.status_code == 403
    assert formula.status == FormulaStatus.DRAFT
    assert session.commits == 0


@pytest.mark.asyncio
async def test_toggle_and_state_transition_allow_delegated_and_super_admin_access(monkeypatch):
    formula_id = uuid4()
    formula = SimpleNamespace(is_active=False, status=FormulaStatus.DRAFT)
    session = _Session()

    async def load(*_args):
        return formula

    async def toggle(_db, target, is_active):
        target.is_active = is_active
        return target

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_: None)
    monkeypatch.setattr(kpi_router, "_load_formula_in_scope", load)
    monkeypatch.setattr(kpi_router.kpi_repo, "toggle_formula_status", toggle)

    assert (await kpi_router.toggle_kpi_formula(formula_id, SimpleNamespace(is_active=True), session, _user("Admin"))).is_active
    transitioned = await kpi_router._transition_formula_state(
        session, formula_id, [FormulaStatus.DRAFT], FormulaStatus.REVIEW, _user("Super Admin")
    )
    assert transitioned.status == FormulaStatus.REVIEW
    assert session.commits == 1


@pytest.mark.asyncio
async def test_calculation_targets_are_scoped_and_reject_mixed_department_ids(monkeypatch):
    user_a, user_b = uuid4(), uuid4()
    session = _Session([user_a])

    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", lambda *_: _async_value({str(uuid4())}))
    admin = _user("Admin")
    assert await kpi_router._authorized_active_user_ids(session, admin) == [user_a]

    admin_with_mixed_users = [user_a, user_b]
    with pytest.raises(HTTPException) as denied:
        await kpi_router._authorized_active_user_ids(
            session, admin, admin_with_mixed_users
        )
    assert denied.value.status_code == 403

    session.values = [user_a, user_b]
    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", lambda *_: _async_value(None))
    assert set(await kpi_router._authorized_active_user_ids(session, _user("Super Admin"))) == {user_a, user_b}


@pytest.mark.asyncio
async def test_upload_rows_reject_out_of_scope_employee_and_allow_global_super_admin(monkeypatch):
    user_a, user_b = uuid4(), uuid4()
    dataframe = pd.DataFrame({"Email": ["a@example.com", "b@example.com"], "Metric": [1, 2]})
    session = _Session([(user_a, "a@example.com")])

    monkeypatch.setattr(upload_router, "get_authorized_department_ids", lambda *_: _async_value({str(uuid4())}))
    rows, errors = await upload_router._build_upload_rows(dataframe, session, _user("Admin"))
    assert [row["user_id"] for row in rows] == [user_a]
    assert errors == ["Row 3: Active user with email 'b@example.com' not found."]

    session.values = [(user_a, "a@example.com"), (user_b, "b@example.com")]
    monkeypatch.setattr(upload_router, "get_authorized_department_ids", lambda *_: _async_value(None))
    rows, errors = await upload_router._build_upload_rows(dataframe, session, _user("Super Admin"))
    assert {row["user_id"] for row in rows} == {user_a, user_b}
    assert errors == []


def test_export_display_uses_only_the_admins_delegated_membership():
    department_a, department_b = uuid4(), uuid4()
    user = SimpleNamespace(
        user_departments=[
            SimpleNamespace(department_id=department_b, is_deleted=False, department=SimpleNamespace(name="Department B")),
            SimpleNamespace(department_id=department_a, is_deleted=False, department=SimpleNamespace(name="Department A")),
        ]
    )
    assert upload_router._visible_department_name(user, {str(department_a)}) == "Department A"
    assert upload_router._visible_department_name(user, None) == "Department B"

def test_export_query_includes_membership_scope_for_admin_but_not_super_admin():
    department_a = uuid4()
    admin_sql = str(upload_router._scoped_active_users_statement(_user("Admin"), {str(department_a)}))
    super_sql = str(upload_router._scoped_active_users_statement(_user("Super Admin"), None))
    assert "user_departments" in admin_sql
    assert "user_departments" not in super_sql

class _FirstScalars(_Scalars):
    def first(self):
        return self._values[0] if self._values else None


class _FirstResult(_Result):
    def scalars(self):
        return _FirstScalars(self._values)

    def scalar_one_or_none(self):
        return self._values[0] if self._values else None


class _QueuedSession:
    def __init__(self, results):
        self._results = list(results)
        self.added = []
        self.commits = 0
        self.rollbacks = 0

    async def execute(self, _statement):
        return _FirstResult(self._results.pop(0))

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        self.commits += 1

    async def rollback(self):
        self.rollbacks += 1

    def expunge(self, _value):
        return None


class _UpdatePayload:
    def __init__(self, department_id):
        self.department_id = department_id
        self.email = None
        self.role_id = None
        self.user_level = None

    def model_dump(self, **_kwargs):
        return {}


@pytest.mark.asyncio
async def test_user_department_update_checks_destination_before_any_mutation(monkeypatch):
    department_a, department_b, user_id = uuid4(), uuid4(), uuid4()
    membership = SimpleNamespace(department_id=department_a, is_deleted=False)
    target = SimpleNamespace(
        id=user_id,
        email="target@example.com",
        role_id=uuid4(),
        role=SimpleNamespace(name="Department User"),
        is_deleted=False,
    )
    session = _QueuedSession([[target], [SimpleNamespace(id=department_b, is_deleted=False)]])
    scope_calls = []

    async def deny_destination(_db, _current_user, department_id):
        scope_calls.append(department_id)
        raise HTTPException(status_code=403, detail="Access denied")

    monkeypatch.setattr(users_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(users_router, "enforce_user_scope", lambda *_args: _async_value(None))
    monkeypatch.setattr(users_router, "enforce_department_scope", deny_destination)
    payload = _UpdatePayload(department_b)
    admin = _user("Admin")

    with pytest.raises(HTTPException) as denied:
        await users_router.update_existing_user(
            user_id, payload, session, admin
        )

    assert denied.value.status_code == 403
    assert scope_calls == [department_b]
    assert membership.department_id == department_a
    assert session.added == []
    assert session.commits == 0


@pytest.mark.asyncio
async def test_user_department_update_allows_delegated_destination(monkeypatch):
    department_a, user_id = uuid4(), uuid4()
    membership = SimpleNamespace(department_id=department_a, user_level="L2", is_deleted=False)
    target = SimpleNamespace(
        id=user_id,
        email="target@example.com",
        first_name="Target",
        last_name="User",
        role_id=uuid4(),
        role=SimpleNamespace(name="Department User"),
        is_deleted=False,
    )
    session = _QueuedSession([
        [target],
        [SimpleNamespace(id=department_a, is_deleted=False)],
        [membership],
    ])
    scope_calls = []

    async def allow_destination(_db, _current_user, department_id):
        scope_calls.append(department_id)

    monkeypatch.setattr(users_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(users_router, "enforce_user_scope", lambda *_args: _async_value(None))
    monkeypatch.setattr(users_router, "enforce_department_scope", allow_destination)
    monkeypatch.setattr(users_router, "record_activity", lambda *_args, **_kwargs: _async_value(None))
    monkeypatch.setattr(users_router.user_repo, "get_user_by_id", lambda *_args: _async_value(target))
    monkeypatch.setattr(users_router, "serialize_user_obj", lambda value: value)

    result = await users_router.update_existing_user(
        user_id, _UpdatePayload(department_a), session, _user("Admin")
    )

    assert result is target
    assert scope_calls == [department_a]
    assert membership.department_id == department_a
    assert session.commits == 1


@pytest.mark.asyncio
async def test_summary_report_uses_delegated_scope_when_department_is_omitted(monkeypatch):
    department_a, department_b = uuid4(), uuid4()
    calls = []

    async def authorized(_db, current_user):
        return None if current_user.role.name == "Super Admin" else {str(department_a)}

    async def scope(_db, _current_user, department_id):
        if department_id == department_b:
            raise HTTPException(status_code=403, detail="Access denied")

    async def generate(**kwargs):
        calls.append(kwargs)
        return {"data": []}

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", authorized)
    monkeypatch.setattr(kpi_router, "enforce_department_scope", scope)
    monkeypatch.setattr(kpi_router.kpi_service.KpiReportService, "generate_summary_report", generate)

    db = _Session()
    admin = _user("Admin")
    await kpi_router.get_kpi_summary_report("MONTHLY", None, db, admin)
    assert calls[-1]["department_id"] is None
    assert calls[-1]["department_ids"] == {str(department_a)}

    with pytest.raises(HTTPException) as denied:
        await kpi_router.get_kpi_summary_report("MONTHLY", department_b, db, admin)
    assert denied.value.status_code == 403
    assert len(calls) == 1

    await kpi_router.get_kpi_summary_report("MONTHLY", None, _Session(), _user("Super Admin"))
    assert calls[-1]["department_ids"] is None


@pytest.mark.asyncio
async def test_report_repository_applies_delegated_department_predicate():
    department_a = uuid4()

    class _StatementSession:
        def __init__(self):
            self.statement = None

        async def execute(self, statement):
            self.statement = statement
            return _Result([])

    session = _StatementSession()
    assert await kpi_repo.get_kpi_report_data(
        session, "MONTHLY", department_ids={str(department_a)}
    ) == []
    assert "department_id IN" in str(session.statement)


@pytest.mark.asyncio
async def test_ranking_query_uses_delegated_department_scope_and_preserves_super_admin(monkeypatch):
    department_a, department_b, user_a, user_b = uuid4(), uuid4(), uuid4(), uuid4()
    record_a = SimpleNamespace(
        department_id=department_a,
        user_id=user_a,
        user=SimpleNamespace(first_name="A", last_name="User"),
        weighted_score=90,
        percentage=90,
        status_bucket=SimpleNamespace(value="A"),
    )
    record_b = SimpleNamespace(
        department_id=department_b,
        user_id=user_b,
        user=SimpleNamespace(first_name="B", last_name="User"),
        weighted_score=80,
        percentage=80,
        status_bucket=SimpleNamespace(value="B"),
    )

    class _RankingSession:
        def __init__(self, records, departments):
            self.records = records
            self.departments = departments
            self.statements = []

        async def execute(self, statement):
            self.statements.append(statement)
            return _Result(self.records if len(self.statements) == 1 else self.departments)

    async def authorized(_db, current_user):
        return None if current_user.role.name == "Super Admin" else {str(department_a)}

    monkeypatch.setattr(kpi_router, "enforce_permission", lambda *_args: None)
    monkeypatch.setattr(kpi_router, "get_authorized_department_ids", authorized)

    admin_session = _RankingSession([record_a], [SimpleNamespace(id=department_a, name="Department A")])
    admin_ranking = await kpi_router.get_kpi_ranking(
        PeriodType.MONTHLY, 10, admin_session, _user("Admin")
    )
    assert [item.user_id for item in admin_ranking] == [user_a]
    assert "department_id IN" in str(admin_session.statements[0])

    super_session = _RankingSession(
        [record_a, record_b],
        [SimpleNamespace(id=department_a, name="Department A"), SimpleNamespace(id=department_b, name="Department B")],
    )
    super_ranking = await kpi_router.get_kpi_ranking(
        PeriodType.MONTHLY, 10, super_session, _user("Super Admin")
    )
    assert {item.user_id for item in super_ranking} == {user_a, user_b}


@pytest.mark.asyncio
async def test_soft_deleted_membership_cannot_validate_new_assignment():
    department_id, position_id, user_id, formula_id, period_id = (uuid4() for _ in range(5))

    class _AssignmentSession:
        def __init__(self):
            self.statement = None

        async def get(self, model, _id):
            if model.__name__ == "Department":
                return SimpleNamespace(is_deleted=False)
            if model.__name__ == "Position":
                return SimpleNamespace(department_id=department_id)
            if model.__name__ == "User":
                return SimpleNamespace(is_active=True, is_deleted=False, position_id=position_id)
            if model.__name__ == "KPIFormula":
                return SimpleNamespace(is_active=True, department_id=department_id)
            return SimpleNamespace(is_deleted=False)

        async def execute(self, statement):
            self.statement = statement
            # A soft-deleted membership is excluded by the SQL predicate.
            return _FirstResult([])

    session = _AssignmentSession()
    with pytest.raises(HTTPException) as denied:
        await assignment_service.validate_assignment_scope(
            session, department_id, position_id, user_id, formula_id, period_id
        )
    assert denied.value.status_code == 400
    assert "is_deleted" in str(session.statement)