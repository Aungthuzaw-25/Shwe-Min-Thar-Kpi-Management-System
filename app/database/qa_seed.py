"""Deterministic, isolated QA fixture seed.

This module is intentionally separate from the broad application seed. It may
only run against the local QA Compose PostgreSQL service.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import AsyncSessionLocal
from app.database.seed import ensure_roles_and_permissions
from app.modules.departments.models import Department, UserDepartment
from app.modules.kpi.models import (
    FormulaScope,
    FormulaStatus,
    KPIAssignment,
    KpiCategory,
    KPIFormula,
    KPIPeriod,
    KPIPeriodStatus,
    KpiRule,
    KPIScoringRuleItem,
)
from app.modules.users.models import Permission, Position, User

QA_DEPARTMENT = "QA Finance Department"
QA_PERIOD = "QA September 2026"
QA_PERIOD_START = datetime(2026, 9, 1, tzinfo=UTC)
QA_PERIOD_END = datetime(2026, 9, 30, 23, 59, 59, tzinfo=UTC)
TIMELINESS = "အချိန်မှီမတင်ပြခြင်း"
QA_SUPERADMIN_EMAIL = "qa.superadmin@example"
LEGACY_QA_SUPERADMIN_EMAIL = "qa.superadmin@example.test"

QA_USERS = {
    QA_SUPERADMIN_EMAIL: ("Super Admin", None, "QA HOD Position", "L0"),
    "qa.admin@example.test": ("Admin", None, "QA HOD Position", "L0"),
    "qa.hr@example.test": ("HR", None, "QA HOD Position", "L0"),
    "qa.l0@example.test": ("Department User", "L0", "QA HOD Position", "L0"),
    "qa.l1@example.test": ("Department User", "L1", "QA Senior Accountant Position", "L1"),
    "qa.l2@example.test": ("Department User", "L2", "QA Accountant Position", "L2"),
}

QA_FORMULA_CATEGORIES = [
    ("Sales Report", 0.30),
    ("Stock Count", 0.34),
    ("Cash Book Report", 0.35),
    (TIMELINESS, 0.01),
]


def _require_qa_target() -> None:
    if settings.ENV.lower() != "qa":
        raise RuntimeError("QA seed requires ENV=qa.")
    database_url = settings.DATABASE_URL.lower()
    if settings.QA_DATABASE_HOST.lower() not in database_url:
        raise RuntimeError("QA seed requires DATABASE_URL to target postgres-qa.")
    if f"/{settings.QA_DATABASE_NAME}" not in database_url:
        raise RuntimeError("QA seed requires DATABASE_URL to target kpi_qa.")
    if settings.QA_DATABASE_PORT != 5432:
        raise RuntimeError("QA seed requires the internal PostgreSQL port 5432.")


def _password_for(email: str) -> str:
    password = os.getenv("QA_SEED_PASSWORD", "").strip()
    if not password:
        raise RuntimeError("Set QA_SEED_PASSWORD to a development-only QA password.")
    return password


async def _get_or_create(db, model, filters: dict, values: dict | None = None):
    instance = (await db.execute(select(model).filter_by(**filters))).scalar_one_or_none()
    if instance is None:
        instance = model(**filters, **(values or {}))
        db.add(instance)
        await db.flush()
    return instance


async def seed_qa() -> dict[str, int]:
    _require_qa_target()
    async with AsyncSessionLocal() as db:
        roles = await ensure_roles_and_permissions(db)
        department = await _get_or_create(db, Department, {"name": QA_DEPARTMENT})

        positions = {}
        for name, parent in (
            ("QA HOD Position", None),
            ("QA Senior Accountant Position", "QA HOD Position"),
            ("QA Accountant Position", "QA Senior Accountant Position"),
        ):
            parent_position = positions.get(parent) if parent else None
            positions[name] = await _get_or_create(
                db,
                Position,
                {"name": name},
                {"department_id": department.id, "parent_position_id": getattr(parent_position, "id", None), "is_active": True},
            )

        users = {}
        for email, (role_name, _level, position_name, membership_level) in QA_USERS.items():
            user = (
                await db.execute(select(User).where(User.email == email))
            ).scalar_one_or_none()
            if user is None and email == QA_SUPERADMIN_EMAIL:
                user = (
                    await db.execute(
                        select(User).where(
                            User.email == LEGACY_QA_SUPERADMIN_EMAIL
                        )
                    )
                ).scalar_one_or_none(                )
                if user is not None:
                    user.email = email

            if user is None:
                user = await _get_or_create(
                    db,
                    User,
                    {"email": email},
                    {
                        "first_name": email.split(".")[1].split("@")[0].title(),
                        "last_name": "QA",
                        "hashed_password": get_password_hash(_password_for(email)),
                        "local_password_enabled": True,
                        "role_id": roles[role_name].id,
                        "position_id": positions[position_name].id,
                        "is_active": True,
                        "is_locked": False,
                        "must_change_password": False,
                    },
                )
            user.hashed_password = get_password_hash(_password_for(email))
            user.local_password_enabled = True
            user.role_id = roles[role_name].id
            user.position_id = positions[position_name].id
            user.is_active = True
            user.is_locked = False
            user.must_change_password = False
            users[email] = user
            await _get_or_create(
                db,
                UserDepartment,
                {"user_id": user.id, "department_id": department.id},
                {"user_level": membership_level},
            )

        department.head_id = users["qa.l0@example.test"].id
        period = await _get_or_create(
            db,
            KPIPeriod,
            {"label": QA_PERIOD},
            {"start_date": QA_PERIOD_START, "end_date": QA_PERIOD_END, "status": KPIPeriodStatus.OPEN},
        )
        period.start_date = QA_PERIOD_START
        period.end_date = QA_PERIOD_END
        period.status = KPIPeriodStatus.OPEN

        categories = {}
        for name, _weight in QA_FORMULA_CATEGORIES:
            categories[name] = await _get_or_create(db, KpiCategory, {"name": name})

        formulas = {}
        for level, email, position_name in (
            ("L0", "qa.l0@example.test", "QA HOD Position"),
            ("L1", "qa.l1@example.test", "QA Senior Accountant Position"),
            ("L2", "qa.l2@example.test", "QA Accountant Position"),
        ):
            formula_name = f"QA Finance {level} Formula"
            category_payload = [
                {
                    "name": name,
                    "description": f"QA {name}",
                    "weight": weight,
                    "max_score": 5.0,
                    "evaluation_type": "DEDUCTION" if name == TIMELINESS else "TARGET",
                    "deductions": (
                        [{"name": "Late submission", "measure_type": "count", "rules": [
                            {"operator": ">=", "value": 1, "deduct_score": 1.0}
                        ]}]
                        if name == TIMELINESS else []
                    ),
                }
                for name, weight in QA_FORMULA_CATEGORIES
            ]
            formula = await _get_or_create(
                db,
                KPIFormula,
                {"name": formula_name, "department_id": department.id, "user_level": level},
                {
                    "description": "Deterministic QA formula",
                    "expression": "weighted_category_score",
                    "position_id": positions[position_name].id,
                    "position_name": position_name,
                    "categories": category_payload,
                    "scope": FormulaScope.ROLE_LEVEL,
                    "is_active": True,
                    "status": FormulaStatus.ACTIVE,
                    "version": 1,
                    "effective_month": "2026-09",
                    "created_by_id": users[QA_SUPERADMIN_EMAIL].id,
                },
            )
            formula.categories = category_payload
            formula.status = FormulaStatus.ACTIVE
            formula.is_active = True
            formulas[level] = formula

            for name, weight in QA_FORMULA_CATEGORIES:
                await _get_or_create(
                    db,
                    KpiRule,
                    {
                        "department_id": department.id,
                        "position_id": positions[position_name].id,
                        "level": level,
                        "category_name": name,
                    },
                    {"weight": weight, "is_active": True},
                )
            await _get_or_create(
                db,
                KPIScoringRuleItem,
                {"formula_id": formula.id, "category_name": TIMELINESS, "rule_name": "Late submission"},
                {"measure_type": "count", "operator": ">=", "target_value": 1.0, "deduct_score": 1.0},
            )
            await _get_or_create(
                db,
                KPIAssignment,
                {"user_id": users[email].id, "kpi_formula_id": formula.id, "kpi_period_id": period.id},
                {
                    "department_id": department.id,
                    "position_id": positions[position_name].id,
                    "assigned_by_id": users[QA_SUPERADMIN_EMAIL].id,
                    "assigned_at": QA_PERIOD_START,
                },
            )

        await db.commit()
        permission_count = len((await db.execute(select(Permission))).scalars().all())
        return {
            "users": len(users),
            "departments": 1,
            "positions": len(positions),
            "permissions": permission_count,
            "kpi_periods": 1,
            "kpi_categories": len(categories),
            "kpi_formulas": len(formulas),
            "kpi_rules": len(formulas) * len(QA_FORMULA_CATEGORIES),
            "kpi_assignments": len(formulas),
            "tasks": 0,
        }


if __name__ == "__main__":
    print(asyncio.run(seed_qa()))
