from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.assignment_service import assign_kpi_to_user, bulk_assign_kpi
from app.modules.kpi.models import KPIAssignment, KPIFormula, KPIPeriod
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_review import TaskReview
from app.modules.users.models import Position, User


class _ScalarResult:
    def __init__(self, values):
        self.values = values

    def scalars(self):
        return self

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None


class _BatchSession:
    def __init__(self, valid_user_ids, memberships, existing_assignments, invalid_user_ids=()):
        self.valid_user_ids = valid_user_ids
        self.memberships = memberships
        self.existing_assignments = existing_assignments
        self.invalid_user_ids = set(invalid_user_ids)
        self.executed_entities = []
        self.added = []
        self.committed = False
        self.rolled_back = False
        self.department_id = uuid4()
        self.position_id = uuid4()
        self.formula_id = uuid4()
        self.period_id = uuid4()

    async def get(self, model, _id):
        if model is Department:
            return SimpleNamespace(is_deleted=False)
        if model is Position:
            return SimpleNamespace(department_id=self.department_id)
        if model is User:
            return SimpleNamespace(
                is_active=True,
                is_deleted=False,
                position_id=self.position_id,
            )
        if model is KPIFormula:
            return SimpleNamespace(is_active=True, department_id=self.department_id)
        if model is KPIPeriod:
            return SimpleNamespace(is_deleted=False)
        raise AssertionError(f"Unexpected model lookup: {model}")

    async def execute(self, statement):
        entity = statement.column_descriptions[0]["entity"]
        self.executed_entities.append(entity)
        if entity is User:
            values = [
                SimpleNamespace(
                    id=user_id,
                    is_active=True,
                    is_deleted=False,
                    position_id=self.position_id,
                )
                for user_id in self.valid_user_ids
                if user_id not in self.invalid_user_ids
            ]
        elif entity is UserDepartment:
            values = self.memberships
        elif entity is KPIAssignment:
            values = self.existing_assignments
        else:
            raise AssertionError(f"Unexpected query entity: {entity}")
        return _ScalarResult(values)

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def rollback(self):
        self.rolled_back = True

    async def refresh(self, _item):
        return None


def _bulk_data(session, user_ids):
    return {
        "department_id": session.department_id,
        "position_id": session.position_id,
        "kpi_formula_id": session.formula_id,
        "kpi_period_id": session.period_id,
        "user_ids": user_ids,
    }


@pytest.mark.asyncio
async def test_bulk_assignment_batches_queries_and_restores_soft_deleted_assignment():
    user_ids = [uuid4(), uuid4()]
    deleted_assignment = SimpleNamespace(
        user_id=user_ids[0],
        is_deleted=True,
    )
    session = _BatchSession(
        user_ids,
        memberships=user_ids,
        existing_assignments=[deleted_assignment],
    )

    assignments = await bulk_assign_kpi(session, _bulk_data(session, user_ids), uuid4())

    assert session.executed_entities == [User, UserDepartment, KPIAssignment]
    assert assignments[0] is deleted_assignment
    assert not deleted_assignment.is_deleted
    assert assignments[1].user_id == user_ids[1]
    assert len(session.added) == 1
    assert session.added[0] is assignments[1]
    assert session.committed


@pytest.mark.asyncio
async def test_single_assignment_restores_soft_deleted_assignment():
    user_id = uuid4()
    deleted_assignment = SimpleNamespace(user_id=user_id, is_deleted=True)
    session = _BatchSession(
        [user_id],
        memberships=[user_id],
        existing_assignments=[deleted_assignment],
    )
    assignment_data = {
        "department_id": session.department_id,
        "position_id": session.position_id,
        "user_id": user_id,
        "kpi_formula_id": session.formula_id,
        "kpi_period_id": session.period_id,
    }

    assignment = await assign_kpi_to_user(session, assignment_data, uuid4())

    assert assignment is deleted_assignment
    assert not assignment.is_deleted
    assert session.committed


@pytest.mark.asyncio
async def test_bulk_validation_failure_does_not_restore_prior_assignment():
    user_ids = [uuid4(), uuid4()]
    deleted_assignment = SimpleNamespace(
        user_id=user_ids[0],
        is_deleted=True,
    )
    session = _BatchSession(
        user_ids,
        memberships=user_ids,
        existing_assignments=[deleted_assignment],
        invalid_user_ids=[user_ids[1]],
    )

    with pytest.raises(HTTPException, match="Selected user is not active"):
        await bulk_assign_kpi(session, _bulk_data(session, user_ids), uuid4())

    assert deleted_assignment.is_deleted
    assert not session.added
    assert not session.committed
    assert not session.rolled_back


def test_task_review_and_evidence_indexes_are_declared_in_model_metadata():
    assert "idx_task_reviews_task_level_created" in {
        index.name for index in TaskReview.__table__.indexes
    }
    assert "ix_task_reviews_task_id" in {
        index.name for index in TaskReview.__table__.indexes
    }
    assert "ix_task_reviews_reviewer_id" in {
        index.name for index in TaskReview.__table__.indexes
    }
    assert "ix_task_evidence_task_id" in {
        index.name for index in TaskEvidence.__table__.indexes
    }
