"""Test-data pack for the Shwe Min Thar KPI system — TEST DATABASES ONLY.

Creates a realistic, deterministic data set on a TEST database so Reports,
departed flows, dashboards and the load smoke test have something real to
chew on: a few departments, ~50-100 employees across departments/levels/
positions, 3 monthly periods (one OPEN, two past), formulas linked to those
periods (categories + targets), hundreds of KPI records with varied scores
(including over-achievement and deduction cases), tasks (completed and
pending), targets, and ONE offboarded (departed, current-year) user.

This script NEVER touches production code and changes no application
behaviour. It only inserts rows through the application's own models.

SAFETY — READ BEFORE RUNNING (also see scripts/README-test-pack.md):
  * NEVER runs on import. Only ``python scripts/test_data_pack.py ...``.
  * A real run requires ALL of: ``--target test``, the environment variable
    ``TEST_DATA_PACK_TARGET=test``, ``--confirm``, and ``--db-name`` equal to
    the database name inside ``DATABASE_URL``. Any missing piece refuses.
  * Production-looking targets are REFUSED: ``ENV=production``, or a
    database host/name containing ``prod`` / ``production`` as a whole
    token (``my-prod-db`` refuses; ``products`` does not).
  * NO hardcoded passwords. The pack password comes from
    ``TEST_PACK_PASSWORD`` when set, otherwise it is generated per run and
    printed ONCE to stdout. It is never written to a file by this script.
  * Reruns are idempotent: existing pack rows (matched by unique email /
    name / label / title) are skipped. ``--reset`` wipes pack rows first,
    ``--wipe`` removes pack rows without reseeding, ``--reset-password``
    only re-hashes pack users' passwords. Wipe/reset match ONLY rows
    carrying the ``TEST-PACK:`` / ``testpack.local`` markers — never
    anything else.

USAGE (from the repo root, with the repo venv active):
  # Preview only — touches nothing, needs no database:
  python scripts/test_data_pack.py --target test --dry-run

  # Real run against the TEST database (Neon test branch or local):
  export TEST_DATA_PACK_TARGET=test
  export DATABASE_URL='postgresql+asyncpg://user:pass@test.example/testdb'
  export TEST_PACK_PASSWORD='a-test-only-password-you-choose'
  python scripts/test_data_pack.py --target test --confirm --db-name testdb

  # Remove everything the pack created, then stop:
  python scripts/test_data_pack.py --target test --confirm --db-name testdb --wipe
"""

from __future__ import annotations

import argparse
import asyncio
import os
import random
import re
import secrets
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from urllib.parse import urlparse

# Running as `python scripts/test_data_pack.py` puts scripts/ on sys.path,
# not the repo root — so make the repo root importable for the app modules
# the real run lazily imports below. (Dry-run never needs it.)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Markers — every row the pack creates carries one of these, so wipe/reset
# can find exactly the pack's rows and nothing else.
# ---------------------------------------------------------------------------
EMAIL_DOMAIN = "testpack.local"
EMAIL_PREFIX = "testpack.user."
OBJECT_PREFIX = "TEST-PACK:"
LEVELS = ("L0", "L1", "L2")
CATEGORY_NAMES = (
    f"{OBJECT_PREFIX} Output Quality",
    f"{OBJECT_PREFIX} Timeliness",
    f"{OBJECT_PREFIX} Teamwork",
    f"{OBJECT_PREFIX} Attendance",
)
TARGET_CATEGORY = f"{OBJECT_PREFIX} Output Quality"
PROD_TOKENS = ("prod", "production")


# ---------------------------------------------------------------------------
# Guards (pure stdlib — unit-checkable without the app or a database)
# ---------------------------------------------------------------------------
def _prod_token_hit(host: str, dbname: str) -> str | None:
    """Return the offending token, or None. Host and db name are split
    separately on - _ . only, so 'products' or 'reproduce' do not trip
    the guard."""
    parts = re.split(r"[-_.]", host.lower()) + re.split(r"[-_.]", dbname.lower())
    for token in PROD_TOKENS:
        if token in parts:
            return token
    return None


def check_guards(database_url: str, db_name: str, env: dict) -> list[str]:
    """Validate a real-run target. Returns a list of refusal reasons
    (empty = allowed). ``env`` is os.environ (injectable for tests)."""
    reasons: list[str] = []
    if not database_url:
        reasons.append("DATABASE_URL is not set.")
        return reasons
    if env.get("ENV", "").lower() == "production":
        reasons.append("ENV=production: refusing to run against production.")
    parsed = urlparse(database_url)
    if parsed.scheme not in ("postgresql+asyncpg", "sqlite+aiosqlite"):
        reasons.append(
            "DATABASE_URL must be an async URL: postgresql+asyncpg:// or "
            "sqlite+aiosqlite:// (the app's async engine needs it)."
        )
        return reasons
    host = (parsed.hostname or "").lower()
    dbname = parsed.path.lstrip("/")
    if dbname.startswith("./"):
        dbname = dbname[2:]  # sqlite relative paths: ./test.db -> test.db
    if (hit := _prod_token_hit(host, dbname)) is not None:
        reasons.append(
            f"Target looks like production (token '{hit}' in host/db name "
            f"'{host}/{dbname}'). Refusing."
        )
    if db_name != dbname:
        reasons.append(
            f"--db-name '{db_name}' does not match the database in "
            f"DATABASE_URL ('{dbname}'). Pass the exact target name."
        )
    return reasons


def require_guards(database_url: str, db_name: str, env: dict) -> None:
    if env.get("TEST_DATA_PACK_TARGET") != "test":
        raise RuntimeError(
            "Refusing to run: set TEST_DATA_PACK_TARGET=test in the "
            "environment (explicit test-only confirmation)."
        )
    if reasons := check_guards(database_url, db_name, env):
        raise RuntimeError("Refusing to run:\n  - " + "\n  - ".join(reasons))


# ---------------------------------------------------------------------------
# Plan — pure data, built without the app or a database (powers --dry-run)
# ---------------------------------------------------------------------------
@dataclass
class PackPlan:
    departments: list[str]
    positions: list[str]
    users: list[dict]  # {email, first_name, last_name, department, level}
    periods: list[dict]  # {label, start, end, status}
    formulas: list[str]
    records: int
    over_achievement_records: int
    deduction_records: int
    tasks: int
    targets: int
    departed_email: str
    departed_resigned_at: date


def _month_bounds(year: int, month: int) -> tuple[datetime, datetime]:
    start = datetime(year, month, 1, tzinfo=UTC)
    if month == 12:
        end = datetime(year + 1, 1, 1, tzinfo=UTC) - timedelta(seconds=1)
    else:
        end = datetime(year, month + 1, 1, tzinfo=UTC) - timedelta(seconds=1)
    return start, end


def _prev_month(year: int, month: int, back: int) -> tuple[int, int]:
    total = year * 12 + (month - 1) - back
    return total // 12, total % 12 + 1


def build_plan(n_users: int, n_departments: int, n_tasks: int, seed: int) -> PackPlan:
    rng = random.Random(seed)
    today = datetime.now(UTC).date()
    dept_names = [f"{OBJECT_PREFIX} Department {chr(65 + i)}" for i in range(n_departments)]
    positions = [
        f"{OBJECT_PREFIX} {name.split()[-1]} {level}"
        for name in dept_names
        for level in LEVELS
    ]
    users = [
        {
            "email": f"{EMAIL_PREFIX}{i + 1:03d}@{EMAIL_DOMAIN}",
            "first_name": f"Pack{i + 1:03d}",
            "last_name": LEVELS[i % len(LEVELS)],
            "department": dept_names[i % n_departments],
            "level": LEVELS[i % len(LEVELS)],
        }
        for i in range(n_users)
    ]
    periods = []
    for back, status in ((0, "OPEN"), (1, "FINALIZED"), (2, "FINALIZED")):
        year, month = _prev_month(today.year, today.month, back)
        start, end = _month_bounds(year, month)
        label = f"{OBJECT_PREFIX} {start.strftime('%B %Y')}"
        periods.append({"label": label, "start": start, "end": end, "status": status})
    formulas = [
        f"{dept} {level}" for dept in dept_names for level in LEVELS
    ]
    records = n_users * len(periods)
    # Score mix, drawn with the SAME function the seeder uses, so the
    # plan counts match the seeded rows exactly. Normal scores bottom
    # out at 42% (raw 2.1/5), deduction scores top out at 40%.
    over = deduction = 0
    for _ in range(records):
        _raw, pct, _bucket, exceeded = _score_for(rng)
        if exceeded is not None:
            over += 1
        elif pct < 41:
            deduction += 1
    # Departed user: the last pack user. Resigned date stays inside the
    # current calendar year (30 days ago, or 15 Jan when that crosses it).
    resigned = today - timedelta(days=30)
    if resigned.year != today.year:
        resigned = date(today.year, 1, 15)
    return PackPlan(
        departments=dept_names,
        positions=positions,
        users=users,
        periods=periods,
        formulas=formulas,
        records=records,
        over_achievement_records=over,
        deduction_records=deduction,
        tasks=n_tasks,
        targets=n_departments + 4,
        departed_email=users[-1]["email"],
        departed_resigned_at=resigned,
    )


def _bucket(percentage: float) -> str:
    if percentage >= 90:
        return "EXCELLENT"
    if percentage >= 80:
        return "VERY_GOOD"
    if percentage >= 60:
        return "GOOD"
    if percentage >= 40:
        return "FAIR"
    return "POOR"


def _score_for(rng: random.Random) -> tuple[float, float, str, list | None]:
    """(raw_score, percentage, bucket, exceeded_categories|None)."""
    roll = rng.random()
    if roll < 0.15:
        # Over-achievement: capped score, excess kept in exceeded_categories.
        if rng.random() < 0.66:
            exceeded = [
                {
                    "category": TARGET_CATEGORY,
                    "direction": "HIGHER",
                    "target_value": 100,
                    "actual_value": round(rng.uniform(112, 130), 1),
                    "exceeded": True,
                    "exceeded_pct": 0.0,  # filled below
                }
            ]
            exceeded[0]["exceeded_pct"] = round(exceeded[0]["actual_value"] - 100, 1)
        else:
            exceeded = [
                {
                    "category": TARGET_CATEGORY,
                    "direction": "LOWER",
                    "target_value": 100,
                    "actual_value": round(rng.uniform(70, 85), 1),
                    "exceeded": True,
                    "exceeded_pct": 0.0,
                }
            ]
            exceeded[0]["exceeded_pct"] = round(100 - exceeded[0]["actual_value"], 1)
        return 5.0, 100.0, "EXCELLENT", exceeded
    if roll < 0.30:
        # Deduction-affected / low score.
        raw = round(rng.uniform(1.0, 2.0), 2)
        pct = round(raw / 5 * 100, 2)
        return raw, pct, _bucket(pct), None
    raw = round(rng.uniform(2.1, 5.0), 2)
    pct = round(raw / 5 * 100, 2)
    return raw, pct, _bucket(pct), None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", required=True,
                        help="Must be exactly 'test'. No default, on purpose.")
    parser.add_argument("--confirm", action="store_true",
                        help="Explicit confirmation that this is a test run.")
    parser.add_argument("--db-name", default="",
                        help="Must equal the database name in DATABASE_URL.")
    parser.add_argument("--users", type=int, default=80,
                        help="Pack employees (default 80; 10-200).")
    parser.add_argument("--departments", type=int, default=4,
                        help="Pack departments (default 4; 1-8).")
    parser.add_argument("--tasks", type=int, default=150,
                        help="Pack tasks (default 150).")
    parser.add_argument("--seed", type=int, default=20261028,
                        help="Deterministic RNG seed (default 20261028).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print the plan only. Touches nothing, needs no DB.")
    parser.add_argument("--reset", action="store_true",
                        help="Wipe pack rows first, then seed.")
    parser.add_argument("--wipe", action="store_true",
                        help="Delete pack rows only, then stop (no seeding).")
    parser.add_argument("--reset-password", action="store_true",
                        help="Only re-hash pack users' passwords, then stop.")
    args = parser.parse_args(argv)
    if args.target != "test":
        parser.error("--target must be exactly 'test'.")
    if not 10 <= args.users <= 200:
        parser.error("--users must be between 10 and 200.")
    if not 1 <= args.departments <= 8:
        parser.error("--departments must be between 1 and 8.")
    if args.tasks < 0:
        parser.error("--tasks must be >= 0.")
    if args.wipe and (args.reset or args.reset_password):
        parser.error("--wipe cannot be combined with --reset/--reset-password.")
    if args.reset and args.reset_password:
        parser.error("--reset cannot be combined with --reset-password.")
    if args.dry_run and (args.wipe or args.reset or args.reset_password):
        parser.error("--dry-run cannot be combined with wipe/reset actions.")
    return args


def print_plan(plan: PackPlan) -> None:
    print("TEST DATA PACK — plan (no changes made)")
    print(f"  departments: {len(plan.departments)} {plan.departments}")
    print(f"  positions: {len(plan.positions)} (per department x L0/L1/L2)")
    print(f"  users: {len(plan.users)} (first {plan.users[0]['email']}, "
          f"last {plan.users[-1]['email']})")
    print("  periods:")
    for period in plan.periods:
        print(f"    - {period['label']} [{period['status']}] "
              f"{period['start'].date()} -> {period['end'].date()}")
    print(f"  formulas: {len(plan.formulas)} (per department x level, "
          f"linked current to all {len(plan.periods)} periods)")
    print(f"  kpi records: {plan.records} (over-achievement "
          f"{plan.over_achievement_records}, deduction/low "
          f"{plan.deduction_records})")
    print(f"  tasks: {plan.tasks} (mix incl. Completed + pending)")
    print(f"  targets: {plan.targets} (company-wide + per-department)")
    print(f"  departed user: {plan.departed_email} "
          f"(RESIGNED {plan.departed_resigned_at}, records/tasks kept)")


# ---------------------------------------------------------------------------
# Real run — app imports live here only, so --dry-run needs no app/DB.
# ---------------------------------------------------------------------------

async def _canonical_email_map(session, user_model) -> dict[str, object]:
    """Pack users keyed by canonical email: the offboarded user's address
    is renamed to deleted_<ts>_<original> by the app, so strip that prefix
    when matching, or a rerun would duplicate the departed user."""
    from sqlalchemy import select

    rows = (
        await session.execute(
            select(user_model).where(user_model.email.like(f"%{EMAIL_DOMAIN}%"))
        )
    ).scalars().all()
    mapping: dict[str, object] = {}
    for user in rows:
        email = user.email
        match = re.match(r"deleted_\d+_(.+)", email)
        mapping[match.group(1) if match else email] = user
    return mapping


async def _wipe_pack(session, m) -> dict[str, int]:
    """Delete ONLY pack-marked rows, FK-safe order. Returns counts."""
    from sqlalchemy import delete, select

    counts: dict[str, int] = {}

    async def _count_delete(stmt, key: str) -> None:
        result = await session.execute(stmt)
        counts[key] = result.rowcount or 0

    pack_user_ids = select(m.User.id).where(m.User.email.like(f"%{EMAIL_DOMAIN}%"))
    pack_formula_ids = select(m.KPIFormula.id).where(
        m.KPIFormula.name.like(f"{OBJECT_PREFIX}%")
    )
    await _count_delete(
        delete(m.KPIRecord).where(m.KPIRecord.user_id.in_(pack_user_ids)),
        "kpi_records_deleted",
    )
    await _count_delete(
        delete(m.KPIAssignment).where(m.KPIAssignment.user_id.in_(pack_user_ids)),
        "kpi_assignments_deleted",
    )
    await _count_delete(
        delete(m.KPIFormulaPeriod).where(
            m.KPIFormulaPeriod.formula_id.in_(pack_formula_ids)
        ),
        "formula_period_links_deleted",
    )
    await _count_delete(
        delete(m.KPIFormula).where(m.KPIFormula.id.in_(pack_formula_ids)),
        "formulas_deleted",
    )
    await _count_delete(
        delete(m.KpiRule).where(m.KpiRule.category_name.like(f"{OBJECT_PREFIX}%")),
        "rules_deleted",
    )
    await _count_delete(
        delete(m.Task).where(m.Task.title.like(f"{OBJECT_PREFIX}%")),
        "tasks_deleted",
    )
    await _count_delete(
        delete(m.KpiCategory).where(m.KpiCategory.name.like(f"{OBJECT_PREFIX}%")),
        "categories_deleted",
    )
    await _count_delete(
        delete(m.Target).where(m.Target.name.like(f"{OBJECT_PREFIX}%")),
        "targets_deleted",
    )
    await _count_delete(
        delete(m.UserDepartment).where(
            m.UserDepartment.user_id.in_(pack_user_ids)
        ),
        "memberships_deleted",
    )
    await _count_delete(
        delete(m.User).where(m.User.email.like(f"%{EMAIL_DOMAIN}%")),
        "users_deleted",
    )
    await _count_delete(
        delete(m.Position).where(m.Position.name.like(f"{OBJECT_PREFIX}%")),
        "positions_deleted",
    )
    await _count_delete(
        delete(m.Department).where(m.Department.name.like(f"{OBJECT_PREFIX}%")),
        "departments_deleted",
    )
    await session.flush()
    return counts


async def _seed(session, m, plan: PackPlan, password_hash: str, rng: random.Random,
                counts: dict) -> None:
    from sqlalchemy import select

    roles = {
        role.name: role
        for role in (await session.execute(select(m.Role))).scalars().all()
    }
    if "Department User" not in roles:
        raise RuntimeError(
            "Required role 'Department User' is missing. Run the application "
            "seed first (it creates roles), then rerun this pack."
        )
    dept_role = roles["Department User"]
    sa_user = next(
        (
            user
            for user in (
                await session.execute(
                    select(m.User).where(
                        m.User.role_id == roles["Super Admin"].id
                    )
                )
            ).scalars().all()
            if user.email
        ),
        None,
    ) if "Super Admin" in roles else None
    sa_id = sa_user.id if sa_user else None

    # Departments + positions (skip existing by name).
    departments: dict[str, object] = {}
    for name in plan.departments:
        dept = (
            await session.execute(
                select(m.Department).where(m.Department.name == name)
            )
        ).scalar_one_or_none()
        if dept is None:
            dept = m.Department(name=name, priority=len(departments))
            session.add(dept)
            counts["departments_created"] = counts.get("departments_created", 0) + 1
        departments[name] = dept
    await session.flush()
    positions: dict[tuple[str, str], object] = {}
    for dept_name in plan.departments:
        for level in LEVELS:
            pname = f"{OBJECT_PREFIX} {dept_name.split()[-1]} {level}"
            pos = (
                await session.execute(
                    select(m.Position).where(m.Position.name == pname)
                )
            ).scalar_one_or_none()
            if pos is None:
                pos = m.Position(
                    name=pname, department_id=departments[dept_name].id,
                    is_active=True,
                )
                session.add(pos)
                counts["positions_created"] = counts.get("positions_created", 0) + 1
            positions[(dept_name, level)] = pos
    await session.flush()

    # Users + memberships (skip existing by canonical email).
    existing = await _canonical_email_map(session, m.User)
    users: dict[str, object] = {}
    for spec in plan.users:
        user = existing.get(spec["email"])
        if user is None:
            user = m.User(
                email=spec["email"],
                hashed_password=password_hash,
                local_password_enabled=True,
                first_name=spec["first_name"],
                last_name=spec["last_name"],
                position_id=positions[(spec["department"], spec["level"])].id,
                role_id=dept_role.id,
                is_active=True,
                is_locked=False,
                must_change_password=False,
            )
            session.add(user)
            counts["users_created"] = counts.get("users_created", 0) + 1
        users[spec["email"]] = user
    await session.flush()
    for spec in plan.users:
        user = users[spec["email"]]
        membership = (
            await session.execute(
                select(m.UserDepartment).where(
                    m.UserDepartment.user_id == user.id,
                    m.UserDepartment.department_id
                    == departments[spec["department"]].id,
                )
            )
        ).scalar_one_or_none()
        if membership is None:
            session.add(
                m.UserDepartment(
                    user_id=user.id,
                    department_id=departments[spec["department"]].id,
                    user_level=spec["level"],
                )
            )
            counts["memberships_created"] = counts.get("memberships_created", 0) + 1
    await session.flush()
    # Department heads: first L0 user of each department, never the
    # departed user (who is the last pack user).
    for dept_name in plan.departments:
        dept = departments[dept_name]
        if dept.head_id is not None:
            continue
        head = next(
            (
                users[spec["email"]]
                for spec in plan.users
                if spec["department"] == dept_name
                and spec["level"] == "L0"
                and spec["email"] != plan.departed_email
            ),
            None,
        )
        if head is not None:
            dept.head_id = head.id
    await session.flush()

    # Categories (skip existing by name).
    category_payload = [
        {
            "name": CATEGORY_NAMES[0], "description": "Pack TARGET category",
            "weight": 0.30, "max_score": 5.0, "evaluation_type": "TARGET",
            "deductions": [],
        },
        {
            "name": CATEGORY_NAMES[1], "description": "Pack TARGET category",
            "weight": 0.30, "max_score": 5.0, "evaluation_type": "TARGET",
            "deductions": [],
        },
        {
            "name": CATEGORY_NAMES[2], "description": "Pack TARGET category",
            "weight": 0.25, "max_score": 5.0, "evaluation_type": "TARGET",
            "deductions": [],
        },
        {
            "name": CATEGORY_NAMES[3], "description": "Pack DEDUCTION category",
            "weight": 0.15, "max_score": 5.0, "evaluation_type": "DEDUCTION",
            "deductions": [{
                "name": "Late Arrival", "measure_type": "count",
                "rules": [{"operator": ">=", "value": 2, "deduct_score": 1.0}],
            }],
        },
    ]
    for payload in category_payload:
        category = (
            await session.execute(
                select(m.KpiCategory).where(m.KpiCategory.name == payload["name"])
            )
        ).scalar_one_or_none()
        if category is None:
            session.add(m.KpiCategory(name=payload["name"]))
            counts["categories_created"] = counts.get("categories_created", 0) + 1
    await session.flush()

    # Periods (skip existing by label).
    period_rows: dict[str, object] = {}
    for spec in plan.periods:
        period = (
            await session.execute(
                select(m.KPIPeriod).where(m.KPIPeriod.label == spec["label"])
            )
        ).scalar_one_or_none()
        if period is None:
            period = m.KPIPeriod(
                label=spec["label"], start_date=spec["start"],
                end_date=spec["end"],
                status=m.KPIPeriodStatus[spec["status"]],
            )
            session.add(period)
            counts["periods_created"] = counts.get("periods_created", 0) + 1
        period_rows[spec["label"]] = period
    await session.flush()

    # Formulas + rules + current period links (skip existing).
    for dept_name in plan.departments:
        for level in LEVELS:
            fname = f"{dept_name} {level}"
            formula = (
                await session.execute(
                    select(m.KPIFormula).where(m.KPIFormula.name == fname)
                )
            ).scalar_one_or_none()
            if formula is None:
                position = positions[(dept_name, level)]
                formula = m.KPIFormula(
                    name=fname,
                    description="Test-data pack formula (test only).",
                    expression="weighted_category_score",
                    department_id=departments[dept_name].id,
                    position_id=position.id,
                    position_name=position.name,
                    user_level=level,
                    categories=category_payload,
                    scope=m.FormulaScope.ROLE_LEVEL,
                    is_active=True,
                    status=m.FormulaStatus.ACTIVE,
                    version=1,
                    effective_month=plan.periods[0]["start"].strftime("%Y-%m"),
                    created_by_id=sa_id,
                )
                session.add(formula)
                counts["formulas_created"] = counts.get("formulas_created", 0) + 1
            await session.flush()
            for payload in category_payload:
                rule = (
                    await session.execute(
                        select(m.KpiRule).where(
                            m.KpiRule.department_id
                            == departments[dept_name].id,
                            m.KpiRule.position_id
                            == positions[(dept_name, level)].id,
                            m.KpiRule.category_name == payload["name"],
                        )
                    )
                ).scalar_one_or_none()
                if rule is None:
                    session.add(
                        m.KpiRule(
                            department_id=departments[dept_name].id,
                            position_id=positions[(dept_name, level)].id,
                            level=level,
                            category_name=payload["name"],
                            weight=payload["weight"],
                            is_active=True,
                        )
                    )
                    counts["rules_created"] = counts.get("rules_created", 0) + 1
            for spec in plan.periods:
                period = period_rows[spec["label"]]
                link = (
                    await session.execute(
                        select(m.KPIFormulaPeriod).where(
                            m.KPIFormulaPeriod.formula_id == formula.id,
                            m.KPIFormulaPeriod.period_id == period.id,
                        )
                    )
                ).scalar_one_or_none()
                if link is None:
                    session.add(
                        m.KPIFormulaPeriod(
                            formula_id=formula.id, period_id=period.id,
                            is_current=True,
                        )
                    )
                    counts["links_created"] = counts.get("links_created", 0) + 1
    await session.flush()

    # Assignments + records (skip existing pairs).
    for spec in plan.users:
        user = users[spec["email"]]
        dept = departments[spec["department"]]
        formula = (
            await session.execute(
                select(m.KPIFormula).where(
                    m.KPIFormula.name
                    == f"{spec['department']} {spec['level']}"
                )
            )
        ).scalar_one()
        for period_spec in plan.periods:
            period = period_rows[period_spec["label"]]
            assignment = (
                await session.execute(
                    select(m.KPIAssignment).where(
                        m.KPIAssignment.user_id == user.id,
                        m.KPIAssignment.kpi_period_id == period.id,
                    )
                )
            ).scalar_one_or_none()
            if assignment is None:
                session.add(
                    m.KPIAssignment(
                        department_id=dept.id,
                        position_id=user.position_id,
                        user_id=user.id,
                        kpi_formula_id=formula.id,
                        kpi_period_id=period.id,
                        assigned_by_id=sa_id,
                        assigned_at=period_spec["start"].replace(tzinfo=None),
                    )
                )
                counts["assignments_created"] = counts.get("assignments_created", 0) + 1
            record = (
                await session.execute(
                    select(m.KPIRecord).where(
                        m.KPIRecord.user_id == user.id,
                        m.KPIRecord.kpi_period_id == period.id,
                    )
                )
            ).scalar_one_or_none()
            if record is None:
                raw, pct, bucket, exceeded = _score_for(rng)
                session.add(
                    m.KPIRecord(
                        user_id=user.id,
                        department_id=dept.id,
                        formula_id=formula.id,
                        kpi_period_id=period.id,
                        employee_name=f"{spec['first_name']} {spec['last_name']}",
                        department_name=spec["department"],
                        period_type=m.PeriodType.MONTHLY,
                        period_start=period_spec["start"],
                        period_end=period_spec["end"],
                        raw_score=raw,
                        weighted_score=pct,
                        percentage=pct,
                        status_bucket=m.KPIStatus[bucket],
                        exceeded_categories=exceeded,
                        is_frozen=True,
                    )
                )
                counts["records_created"] = counts.get("records_created", 0) + 1
    await session.flush()

    # Tasks (skip existing by title).
    task_statuses = [
        m.TaskStatus.COMPLETED, m.TaskStatus.COMPLETED,
        m.TaskStatus.COMPLETED, m.TaskStatus.COMPLETED,
        m.TaskStatus.PENDING_L1, m.TaskStatus.PENDING_L1,
        m.TaskStatus.PENDING_L0, m.TaskStatus.APPROVED,
        m.TaskStatus.DRAFT, m.TaskStatus.REJECTED,
    ]
    now = datetime.now(UTC).replace(tzinfo=None)
    for index in range(plan.tasks):
        title = f"{OBJECT_PREFIX} Task {index + 1:05d}"
        task = (
            await session.execute(select(m.Task).where(m.Task.title == title))
        ).scalar_one_or_none()
        if task is not None:
            continue
        owner_spec = plan.users[index % len(plan.users)]
        owner = users[owner_spec["email"]]
        category = CATEGORY_NAMES[index % len(CATEGORY_NAMES)]
        session.add(
            m.Task(
                title=title,
                description=f"Test-data pack task #{index + 1} (test only).",
                category=category,
                status=task_statuses[index % len(task_statuses)],
                due_date=now + timedelta(days=rng.randint(-30, 30)),
                grace_period_days=index % 4,
                is_late=(index % 7 == 0),
                late_days=index % 6,
                owner_id=owner.id,
                assigner_id=sa_id or owner.id,
                version=1,
                frequency=m.TaskFrequency.ONCE,
                created_at=now - timedelta(days=rng.randint(0, 180)),
            )
        )
        counts["tasks_created"] = counts.get("tasks_created", 0) + 1
    await session.flush()

    # Targets (skip existing by name + month).
    target_specs: list[tuple[str, str | None, str, str]] = []
    current_month = plan.periods[0]["start"].strftime("%Y-%m")
    prev_month = plan.periods[1]["start"].strftime("%Y-%m")
    prev2_month = plan.periods[2]["start"].strftime("%Y-%m")
    target_specs.append((f"{OBJECT_PREFIX} Company target {current_month}",
                         None, current_month, "ACTIVE"))
    target_specs.append((f"{OBJECT_PREFIX} Company target {prev_month}",
                         None, prev_month, "ACTIVE"))
    for dept_name in plan.departments:
        target_specs.append((f"{dept_name} target {current_month}",
                             dept_name, current_month, "ACTIVE"))
    target_specs.append((f"{OBJECT_PREFIX} Company target {prev2_month} achieved",
                         None, prev2_month, "ACHIEVED"))
    target_specs.append((f"{OBJECT_PREFIX} Company target {prev2_month} missed",
                         None, prev2_month, "MISSED"))
    for name, dept_name, month, status in target_specs:
        target = (
            await session.execute(
                select(m.Target).where(
                    m.Target.name == name, m.Target.target_month == month
                )
            )
        ).scalar_one_or_none()
        if target is None:
            session.add(
                m.Target(
                    name=name,
                    department_id=departments[dept_name].id if dept_name else None,
                    target_value=round(rng.uniform(50, 500), 1),
                    metric_type=m.MetricType.COUNT,
                    target_month=month,
                    status=m.TargetStatus[status],
                    created_by_id=sa_id,
                )
            )
            counts["targets_created"] = counts.get("targets_created", 0) + 1
    await session.flush()

    # Departed user LAST: mirror the app's offboard field changes
    # (status + leaving date + login off + soft-delete + renamed email +
    # resigned snapshots on kept records/tasks). Grants: the pack user
    # holds none, so there is nothing to revoke. Audit rows are the
    # app's runtime concern, not seed data.
    departed = users[plan.departed_email]
    if departed.status != "RESIGNED":
        departed.status = "RESIGNED"
        departed.resigned_at = plan.departed_resigned_at
        departed.is_active = False
        departed.is_deleted = True
        if not departed.email.startswith("deleted_"):
            departed.email = (
                f"deleted_{int(datetime.now(UTC).timestamp())}_{departed.email}"
            )
        for record in (
            await session.execute(
                select(m.KPIRecord).where(m.KPIRecord.user_id == departed.id)
            )
        ).scalars().all():
            record.employee_resigned_at = plan.departed_resigned_at
        for task in (
            await session.execute(
                select(m.Task).where(m.Task.owner_id == departed.id)
            )
        ).scalars().all():
            task.owner_resigned_at = plan.departed_resigned_at
        counts["departed_transitioned"] = 1
    await session.flush()


async def _run(args: argparse.Namespace, env: dict) -> dict:
    """Lazy app imports live here so --dry-run never needs the app."""
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.security import get_password_hash
    from app.modules.departments.models import Department, UserDepartment
    from app.modules.kpi.models import (
        FormulaScope,
        FormulaStatus,
        KPIAssignment,
        KPIFormula,
        KPIFormulaPeriod,
        KPIPeriod,
        KPIPeriodStatus,
        KPIRecord,
        KPIStatus,
        KpiCategory,
        KpiRule,
        PeriodType,
    )
    from app.modules.targets.models import MetricType, Target, TargetStatus
    from app.modules.tasks.models.task import (
        Task,
        TaskFrequency,
        TaskStatus,
    )
    from app.modules.users.models import Position, Role, User

    class M:  # namespace so _seed/_wipe stay model-agnostic
        pass

    m = M()
    for name, cls in {
        "Department": Department, "UserDepartment": UserDepartment,
        "FormulaScope": FormulaScope, "FormulaStatus": FormulaStatus,
        "KPIAssignment": KPIAssignment, "KPIFormula": KPIFormula,
        "KPIFormulaPeriod": KPIFormulaPeriod, "KPIPeriod": KPIPeriod,
        "KPIPeriodStatus": KPIPeriodStatus, "KPIRecord": KPIRecord,
        "KPIStatus": KPIStatus, "KpiCategory": KpiCategory, "KpiRule": KpiRule,
        "PeriodType": PeriodType, "MetricType": MetricType,
        "Target": Target, "TargetStatus": TargetStatus, "Task": Task,
        "TaskFrequency": TaskFrequency, "TaskStatus": TaskStatus,
        "Position": Position, "Role": Role, "User": User,
    }.items():
        setattr(m, name, cls)

    database_url = env.get("DATABASE_URL", "")
    parsed = urlparse(database_url)
    connect_args: dict = {}
    if parsed.scheme == "postgresql+asyncpg":
        connect_args["statement_cache_size"] = 0
        if (parsed.hostname or "").lower() not in (
            "localhost", "127.0.0.1", "::1",
        ) and env.get("TEST_PACK_SSL", "true").lower() != "false":
            connect_args["ssl"] = True
    engine = create_async_engine(
        database_url, pool_pre_ping=True, connect_args=connect_args
    )
    if parsed.scheme == "sqlite+aiosqlite":
        # SQLite has no gen_random_uuid() (KpiCategory's id default is a
        # Postgres function). Register a uuid4 equivalent per connection
        # so local sqlite test targets work. Postgres needs no shim.
        # Safe for the pack: categories are looked up by name, never id.
        import uuid as _uuid

        from sqlalchemy import event as _sa_event

        def _register_gen_random_uuid(dbapi_conn, _record):
            dbapi_conn.create_function(
                "gen_random_uuid", 0, lambda: str(_uuid.uuid4())
            )

        _sa_event.listen(
            engine.sync_engine, "connect", _register_gen_random_uuid
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    counts: dict = {}
    try:
        async with factory() as session:
            if args.wipe or args.reset:
                wiped = await _wipe_pack(session, m)
                counts.update(wiped)
                if args.wipe:
                    await session.commit()
                    return counts
            if args.reset_password:
                password = env.get("TEST_PACK_PASSWORD", "").strip()
                generated = False
                if not password:
                    password = secrets.token_urlsafe(18)
                    generated = True
                password_hash = get_password_hash(password)
                existing = await _canonical_email_map(session, m.User)
                changed = 0
                for user in existing.values():
                    user.hashed_password = password_hash
                    changed += 1
                await session.commit()
                counts["passwords_reset"] = changed
                counts["password_generated"] = generated
                counts["password_value"] = password if generated else None
                return counts
            password = env.get("TEST_PACK_PASSWORD", "").strip()
            generated = False
            if not password:
                password = secrets.token_urlsafe(18)
                generated = True
            password_hash = get_password_hash(password)
            plan = build_plan(args.users, args.departments, args.tasks, args.seed)
            rng = random.Random(args.seed)
            await _seed(session, m, plan, password_hash, rng, counts)
            await session.commit()
            counts["password_generated"] = generated
            counts["password_value"] = password if generated else None
            counts["departed_email_original"] = plan.departed_email
            counts["departed_resigned_at"] = plan.departed_resigned_at.isoformat()
            return counts
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    plan = build_plan(args.users, args.departments, args.tasks, args.seed)
    if args.dry_run:
        if args.target != "test":
            print("Refusing: --target must be 'test'.", file=sys.stderr)
            return 2
        print_plan(plan)
        print("Dry run only — nothing was written, no database was touched.")
        return 0
    if not args.confirm:
        print("Refusing: pass --confirm to run against a test database.",
              file=sys.stderr)
        return 2
    if not args.db_name:
        print("Refusing: pass --db-name equal to the DATABASE_URL database.",
              file=sys.stderr)
        return 2
    try:
        require_guards(os.environ.get("DATABASE_URL", ""), args.db_name,
                       os.environ)
    except RuntimeError as exc:
        print(f"Refusing: {exc}", file=sys.stderr)
        return 2
    try:
        counts = asyncio.run(_run(args, os.environ))
    except RuntimeError as exc:
        print(f"Failed: {exc}", file=sys.stderr)
        return 1
    print("TEST DATA PACK — done")
    for key in sorted(counts):
        if key in ("password_value", "password_generated"):
            continue
        print(f"  {key}={counts[key]}")
    if counts.get("password_generated"):
        print("  TEST-ONLY pack password (generated, printed once, NOT saved):")
        print(f"  {counts['password_value']}")
        print("  Save it if you need to log in as a pack user; rerun with")
        print("  --reset-password (and TEST_PACK_PASSWORD set) to change it.")
    elif not (args.wipe or args.reset_password):
        print("  Pack password: from TEST_PACK_PASSWORD (not printed).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
