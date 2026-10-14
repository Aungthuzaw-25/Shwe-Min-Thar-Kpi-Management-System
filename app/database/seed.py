import asyncio
import calendar
import os
import sys
import uuid
from datetime import UTC, datetime

from sqlalchemy import text

# Project Root Directory ကို System Path ထဲထည့်ရန်
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.future import select

from app.core.config import settings
from app.core.security import get_password_hash
from app.database.connection import AsyncSessionLocal
from app.modules.departments.models import Department, UserDepartment

# 🌟 Import ALL KPI Models correctly for Fully Relational Ecosystem
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
from app.modules.users.models import Permission, Position, Role, RolePermission, User

ROLES = [
    {"name": "Super Admin", "description": "Has full system control"},
    {"name": "Admin", "description": "Manages assigned organization"},
    {"name": "HR", "description": "Human resources administration"},
    {"name": "Department User", "description": "Operational users (L0, L1, L2)"}
]
SENIOR_ACCOUNTANT_POSITION = "Senior Accountant"

def resolve_seed_password(*candidate_names: str) -> str | None:
    for name in candidate_names:
        value = os.getenv(name) or getattr(settings, name, None)
        if value and value.strip():
            return value
    return None


def resolve_user_password(email: str) -> str | None:
    password_map = {
        "cashier.mdy@kpi.com": (
            "SEED_L2_PASSWORD",
            "UI_TEST_L2_PASSWORD",
            "SEED_DEMO_PASSWORD",
        ),
        "finance.l0@kpi.com": (
            "SEED_L0_PASSWORD",
            "UI_TEST_L0_PASSWORD",
            "SEED_DEMO_PASSWORD",
        ),
        "finance.l1@kpi.com": (
            "SEED_L1_PASSWORD",
            "UI_TEST_L1_PASSWORD",
            "SEED_DEMO_PASSWORD",
        ),
    }
    return resolve_seed_password(*password_map.get(email, ("SEED_DEMO_PASSWORD",)))


def resolve_test_account(name: str) -> tuple[str, str] | None:
    if not getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False):
        return None
    email = (os.getenv(f"SEED_TEST_{name}_EMAIL") or getattr(settings, f"SEED_TEST_{name}_EMAIL", None) or "").strip()
    password = resolve_seed_password(f"SEED_TEST_{name}_PASSWORD")
    if not email or not password:
        return None
    return email, password

PERMISSION_CATALOG = [
    ("users.view", "View users"), ("users.create", "Create users"), ("users.edit", "Edit users"),
    ("users.delete", "Delete users"), ("users.activate", "Activate users"), ("users.deactivate", "Deactivate users"),
    ("users.password_reset", "Reset user passwords"),
    ("tasks.view", "View tasks"), ("tasks.create", "Create tasks"), ("tasks.edit", "Edit tasks"),
    ("tasks.delete", "Delete tasks"), ("tasks.submit", "Submit tasks"), ("tasks.resubmit", "Resubmit tasks"), ("tasks.approve", "Approve tasks"),
    ("tasks.reject", "Reject tasks"), ("tasks.return", "Return tasks"),
    ("kpi.view", "View KPIs"), ("kpi.create", "Create KPIs"), ("kpi.edit", "Edit KPIs"),
    ("kpi.delete", "Delete KPIs"), ("kpi.assign", "Assign KPIs"), ("kpi.calculate", "Calculate KPIs"),
    ("kpi.rule.view", "View KPI rules"), ("kpi.rule.create", "Create KPI rules"),
    ("kpi.rule.edit", "Edit KPI rules"), ("kpi.rule.delete", "Delete KPI rules"),
    ("report.view", "View reports"), ("report.create", "Create reports"), ("report.export", "Export reports"),
    ("report.view_all", "View other users' reports"),
    ("department.view", "View departments"), ("department.create", "Create departments"),
    ("department.edit", "Edit departments"), ("department.delete", "Delete departments"),
    ("system.settings.view", "View settings"), ("system.settings.edit", "Edit settings"),
    ("system.audit.view", "View audit history"),
    ("system.permissions.manage", "Manage role permissions and user overrides"),
]

ROLE_DEFAULTS = {
    "Super Admin": {code for code, _ in PERMISSION_CATALOG},
    "Admin": {code for code, _ in PERMISSION_CATALOG}
    - {"system.settings.edit", "system.audit.view"},
    "HR": {
        "users.view", "users.create", "users.edit", "users.activate",
        "users.deactivate", "users.password_reset", "department.view",
        "kpi.view", "report.view", "report.view_all",
        "system.permissions.manage",
    },
    "Department User": {
        "users.view", "tasks.view", "tasks.create", "tasks.edit",
        "tasks.delete", "tasks.submit", "tasks.resubmit",
        "tasks.approve", "tasks.reject", "tasks.return",
        "kpi.view", "report.view", "department.view",
    },
}

# Common Deduction Rules
STANDARD_ERROR_RULES = [
    {"operator": "==", "value": 1, "deduct_score": 2.0},  
    {"operator": ">=", "value": 2, "deduct_score": 4.0}   
]

STRICT_ERROR_RULES = [
    {"operator": "==", "value": 1, "deduct_score": 3.0},  
    {"operator": ">=", "value": 2, "deduct_score": 5.0}   
]

VOUCHER_PERCENTAGE_RULES = [
    {"operator": "<", "value": 98.0, "deduct_score": 2.0},  
    {"operator": "<", "value": 95.0, "deduct_score": 4.0}   
]

ATTENDANCE_RULES = [
    {"name": "ရုံးနောက်ကျခြင်း", "measure_type": "count", "rules": [
        {"operator": "==", "value": 1, "deduct_score": 1.0},
        {"operator": "==", "value": 2, "deduct_score": 2.0},
        {"operator": ">=", "value": 3, "deduct_score": 4.0}
    ]},
    {"name": "ခွင့်မဲ့ပျက်ကွက်မှု", "measure_type": "count", "rules": [
        {"operator": ">=", "value": 1, "deduct_score": 4.0} 
    ]}
]

SEED_ADVISORY_LOCK_KEY = 731942

async def _seed_permissions(db):
    """Seed the permission catalog without overwriting existing configuration."""
    bind = db.get_bind()
    catalog = dict(PERMISSION_CATALOG)
    if bind.dialect.name == "postgresql":
        await db.execute(
            pg_insert(Permission)
            .values(
                [
                    {"code": code, "description": description}
                    for code, description in catalog.items()
                ]
            )
            .on_conflict_do_nothing(index_elements=["code"])
        )
    else:
        permissions = (
            await db.execute(
                select(Permission).where(Permission.code.in_(catalog))
            )
        ).scalars().all()
        existing_codes = {permission.code for permission in permissions}
        for code, description in catalog.items():
            if code not in existing_codes:
                db.add(Permission(code=code, description=description))
    await db.commit()


async def ensure_roles_and_permissions(db):
    """Ensure the canonical RBAC catalog exists without overwriting custom grants."""
    await db.flush()
    permission_rows = (
        await db.execute(
            select(Permission).where(
                Permission.code.in_([code for code, _ in PERMISSION_CATALOG])
            )
        )
    ).scalars().all()
    permissions_by_code = {permission.code: permission for permission in permission_rows}
    for code, description in PERMISSION_CATALOG:
        if code not in permissions_by_code:
            permission = Permission(code=code, description=description)
            db.add(permission)
            await db.flush()
            permissions_by_code[code] = permission

    roles_by_name = {}
    for role_data in ROLES:
        role = (
            await db.execute(select(Role).where(Role.name == role_data["name"]))
        ).scalar_one_or_none()
        if role is None:
            role = Role(**role_data)
            db.add(role)
            await db.flush()
        roles_by_name[role.name] = role

    for role_name, codes in ROLE_DEFAULTS.items():
        role = roles_by_name[role_name]
        links = (
            await db.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )
        ).scalars().all()
        existing_permission_ids = {link.permission_id for link in links}
        for code in codes:
            permission = permissions_by_code[code]
            if permission.id not in existing_permission_ids:
                db.add(RolePermission(role_id=role.id, permission_id=permission.id))
    await db.flush()
    return roles_by_name


async def _ensure_super_admin(db, role_id):
    """Create the first Super Admin without changing an existing account."""
    existing_super_admin = (
        await db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(Role.name == "Super Admin")
            .limit(1)
        )
    ).scalar_one_or_none()
    if existing_super_admin:
        print("Super Admin already exists; preserving its credential and account state.")
        return False

    sa_email = (
        os.getenv("SEED_SUPERADMIN_EMAIL")
        or getattr(settings, "SEED_SUPERADMIN_EMAIL", None)
        or os.getenv("BOOTSTRAP_SUPERADMIN_EMAIL")
        or getattr(settings, "BOOTSTRAP_SUPERADMIN_EMAIL", None)
        or ""
    ).strip()
    bootstrap_password = resolve_seed_password(
        "SEED_SUPERADMIN_PASSWORD",
        "SEED_SUPER_ADMIN_PASSWORD",
        "BOOTSTRAP_SUPERADMIN_PASSWORD",
    )
    if not sa_email or not bootstrap_password:
        raise RuntimeError(
            "Super Admin is missing. Set SEED_SUPERADMIN_EMAIL and "
            "SEED_SUPERADMIN_PASSWORD before running the explicit seed."
        )

    db.add(
        User(
            email=sa_email,
            first_name="System",
            last_name="Super Admin",
            hashed_password=get_password_hash(bootstrap_password),
            role_id=role_id,
            is_active=True,
            must_change_password=False,
        )
    )
    await db.commit()
    print("Super Admin created.")
    return True


async def _seed_departments(db):
    departments = [
        {"name": "Human Resources"},
        {"name": "Information Technology"},
        {"name": "Finance"},
        {"name": "Finance & Account"},
    ]
    dept_dict = {}
    for dept_data in departments:
        stmt = select(Department).where(Department.name == dept_data["name"])
        existing_dept = (await db.execute(stmt)).scalar_one_or_none()
        if not existing_dept:
            dept = Department(**dept_data)
            db.add(dept)
            await db.commit()
            await db.refresh(dept)
            dept_dict[dept.name] = dept
        else:
            dept_dict[existing_dept.name] = existing_dept
    print("Departments seeded!")
    return dept_dict


async def _get_active_super_admin(db, role_id):
    await _ensure_super_admin(db, role_id)
    sa_user = (
        await db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(
                Role.name == "Super Admin",
                User.is_deleted.is_(False),
                User.is_active.is_(True),
            )
            .order_by(User.created_at.asc())
            .limit(1)
        )
    ).scalars().first()
    if sa_user is None:
        raise RuntimeError(
            "KPI seeding requires an active Super Admin creator. "
            "Configure the seed/bootstrap Super Admin credentials."
        )
    return sa_user


async def _get_or_create_active_period(db):
    target_date = datetime.now(UTC)
    year, month = target_date.year, target_date.month
    period_start = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
    last_day = calendar.monthrange(year, month)[1]
    period_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
    period_label = target_date.strftime("%B %Y")

    stmt = select(KPIPeriod).where(KPIPeriod.label == period_label)
    period = (await db.execute(stmt)).scalars().first()
    if not period:
        period = KPIPeriod(
            label=period_label,
            start_date=period_start,
            end_date=period_end,
            status=KPIPeriodStatus.OPEN,
        )
        db.add(period)
        await db.commit()
        await db.refresh(period)
        print(f"Active KPI Period '{period_label}' created!")
    return period


async def _ensure_formula_position(db, position_name, department_id):
    position = (
        await db.execute(select(Position).where(Position.name == position_name))
    ).scalars().first()
    if not position:
        position = Position(name=position_name, department_id=department_id)
        db.add(position)
        await db.commit()
        await db.refresh(position)
    return position


async def _ensure_formula_category_and_rule(
    db, category, department_id, position_id, level
):
    category_name = category.get("name")
    if not category_name:
        return

    stmt_cat = select(KpiCategory).where(KpiCategory.name == category_name)
    existing_cat = (await db.execute(stmt_cat)).scalar_one_or_none()
    if not existing_cat:
        new_cat = KpiCategory(name=category_name)
        db.add(new_cat)
        await db.commit()
        await db.refresh(new_cat)

    stmt_rule = select(KpiRule).where(
        KpiRule.department_id == department_id,
        KpiRule.position_id == position_id,
        KpiRule.level == level,
        KpiRule.category_name == category_name,
    )
    existing_rule = (await db.execute(stmt_rule)).scalar_one_or_none()
    if not existing_rule:
        new_rule = KpiRule(
            department_id=department_id,
            position_id=position_id,
            level=level,
            category_name=category_name,
            weight=category.get("weight", 0.0),
            is_active=True,
        )
        db.add(new_rule)
        await db.commit()
        await db.refresh(new_rule)


def _scoring_rule_items(formula_id, categories):
    rule_items = []
    for category in categories:
        category_name = category.get("name")
        for deduction in category.get("deductions", []):
            rule_name = deduction.get("name", "Unknown Rule")
            measure_type = deduction.get("measure_type", "count")
            for rule in deduction.get("rules", []):
                rule_items.append(
                    KPIScoringRuleItem(
                        formula_id=formula_id,
                        category_name=category_name,
                        rule_name=rule_name,
                        measure_type=measure_type,
                        operator=rule.get("operator", "=="),
                        target_value=float(rule.get("value", 0)),
                        deduct_score=float(rule.get("deduct_score", 0)),
                    )
                )
    return rule_items


async def _prepare_formula(db, formula_data, finance_dept_id, creator_id):
    with db.no_autoflush:
        position = await _ensure_formula_position(
            db, formula_data["pos"], finance_dept_id
        )
        stmt = select(KPIFormula).where(
            KPIFormula.department_id == finance_dept_id,
            KPIFormula.position_name == formula_data["pos"],
            KPIFormula.region_town == formula_data["region"],
        )
        existing = (await db.execute(stmt)).scalars().first()

        if existing:
            print(f"Skipped: {formula_data['formula_title']} (Already Exists)")
            return (
                None,
                [],
                {
                    "formula_id": existing.id,
                    "position_id": position.id,
                },
            )

        formula = KPIFormula()
        formula.id = uuid.uuid4()
        formula.name = formula_data["formula_title"]
        formula.description = (
            f"KPI formula for {formula_data['pos']} in {formula_data['region']}"
        )
        formula.expression = None
        formula.department_id = finance_dept_id
        formula.position_name = formula_data["pos"]
        formula.position_id = position.id
        formula.region_town = formula_data["region"]
        formula.user_level = formula_data["lvl"]
        formula.categories = formula_data["cats"]
        formula.status = FormulaStatus.ACTIVE
        formula.scope = FormulaScope.DEPARTMENT
        formula.is_active = True
        formula.version = 1
        formula.created_by_id = creator_id

        for category in formula_data["cats"]:
            await _ensure_formula_category_and_rule(
                db,
                category,
                finance_dept_id,
                position.id,
                formula_data["lvl"],
            )

        rule_items = _scoring_rule_items(formula.id, formula_data["cats"])
        print(f"Prepared Formula & Rules: {formula_data['formula_title']}")
        return (
            formula,
            rule_items,
            {
                "formula_id": formula.id,
                "position_id": position.id,
            },
        )


async def _prepare_formulas(db, formulas, finance_dept_id, creator_id):
    formulas_to_add = []
    rules_to_add = []
    created_formula_maps = {}
    for formula_data in formulas:
        formula, rule_items, formula_map = await _prepare_formula(
            db, formula_data, finance_dept_id, creator_id
        )
        if formula is not None:
            formulas_to_add.append(formula)
        rules_to_add.extend(rule_items)
        created_formula_maps[formula_data["formula_title"]] = formula_map
    return formulas_to_add, rules_to_add, created_formula_maps


async def _insert_formulas_and_scoring_rules(db, formulas, rule_items):
    for formula in formulas:
        db.add(formula)
        await db.commit()

    batch_size = 100
    for start in range(0, len(rule_items), batch_size):
        db.add_all(rule_items[start:start + batch_size])
        await db.commit()


async def _backfill_formula_categories(db):
    all_formulas = (await db.execute(select(KPIFormula))).scalars().all()
    missing_categories = set()
    for formula in all_formulas:
        for category in formula.categories or []:
            category_name = (
                category.get("name", "").strip()
                if isinstance(category, dict)
                else ""
            )
            if category_name:
                missing_categories.add(category_name)
    if not missing_categories:
        return

    existing_categories = set(
        (
            await db.execute(
                select(KpiCategory.name).where(
                    KpiCategory.name.in_(missing_categories)
                )
            )
        ).scalars().all()
    )
    db.add_all(
        KpiCategory(name=name)
        for name in sorted(missing_categories - existing_categories)
    )
    await db.commit()


async def _run_seed():
    async with AsyncSessionLocal() as db:
        print("Starting database seed...")

        await _seed_permissions(db)
        print("Permission catalog seeded!")

        # 1. Departments
        departments = [
            {"name": "Human Resources"},
            {"name": "Information Technology"},
            {"name": "Finance"},
            {"name": "Finance & Account"} 
        ]
        
        dept_dict = {}
        for dept_data in departments:
            stmt = select(Department).where(Department.name == dept_data["name"])
            existing_dept = (await db.execute(stmt)).scalar_one_or_none()
            if not existing_dept:
                dept = Department(**dept_data)
                db.add(dept)
                await db.commit()
                await db.refresh(dept)
                dept_dict[dept.name] = dept
            else:
                dept_dict[existing_dept.name] = existing_dept
                
        print("Departments seeded!")

        # 2. Roles and permissions
        role_dict = await ensure_roles_and_permissions(db)
        await db.commit()

        # 3. Super Admin bootstrap (explicit seed only; create-only).
        await _ensure_super_admin(db, role_dict["Super Admin"].id)
        # KPI formulas and assignments are authored by the existing internal
        # Super Admin, not by the demo employee used for assignment coverage.
        sa_user = (
            await db.execute(
                select(User)
                .join(Role, User.role_id == Role.id)
                .where(
                    Role.name == "Super Admin",
                    User.is_deleted.is_(False),
                    User.is_active.is_(True),
                )
                .order_by(User.created_at.asc())
                .limit(1)
            )
        ).scalars().first()
        if sa_user is None:
            raise RuntimeError(
                "KPI seeding requires an active Super Admin creator. "
                "Configure the seed/bootstrap Super Admin credentials."
            )

        # 🌟 4. KPI Period Setup (Active Cycle) 🌟
        target_date = datetime.now(UTC)
        year, month = target_date.year, target_date.month
        period_start = datetime(year, month, 1, 0, 0, 0, tzinfo=UTC)
        last_day = calendar.monthrange(year, month)[1]
        period_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=UTC)
        period_label = target_date.strftime("%B %Y")
        
        stmt = select(KPIPeriod).where(KPIPeriod.label == period_label)
        period = (await db.execute(stmt)).scalars().first()
        if not period:
            period = KPIPeriod(
                label=period_label, 
                start_date=period_start, 
                end_date=period_end, 
                status=KPIPeriodStatus.OPEN
            )
            db.add(period)
            await db.commit()
            await db.refresh(period)
            print(f"Active KPI Period '{period_label}' created!")

        # 5. KPI Formulas & Scoring Rules Seeding
        print("Starting KPI Formula Seeding...")
        finance_dept_id = dept_dict["Finance & Account"].id
        
        formulas = [
            {"formula_title": "Cashier KPI (MDY)", "pos": "Cashier", "region": "MDY Office", "lvl": "L2", "cats": [
                {"name": "Cash Handling Accuracy & Reconciliation", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "စာရင်းလွဲချော်မှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Voucher Management & Ledger Entry", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ဘောက်ချာမှန်ကန်မှု ရာခိုင်နှုန်း", "measure_type": "percentage", "rules": VOUCHER_PERCENTAGE_RULES}]},
                {"name": "Compliance with Financial Policies", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "စည်းကမ်းဖောက်ဖျက်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cash Management & External Execution", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "ဘဏ်အပ်နှံမှု နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Support", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Cashier KPI (YGN)", "pos": "Cashier", "region": "YGN Office", "lvl": "L2", "cats": [
                {"name": "Cash Handling Accuracy & Reconciliation", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "စာရင်းလွဲချော်မှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Voucher Management & Ledger Entry", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ဘောက်ချာမှန်ကန်မှု ရာခိုင်နှုန်း", "measure_type": "percentage", "rules": VOUCHER_PERCENTAGE_RULES}]},
                {"name": "Compliance with Financial Policies", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "စည်းကမ်းဖောက်ဖျက်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cash Management & External Execution", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "ဘဏ်အပ်နှံမှု နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Support", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Cash Accountant KPI", "pos": "Cash Accountant", "region": "MDY Office", "lvl": "L2", "cats": [
                {"name": "Cash & Bank Accuracy & Reconciliation", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": "စာရင်းလွဲချော်မှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Verification", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "Report နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Compliance, Documentation & Security", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "စည်းကမ်းဖောက်ဖျက်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Audit Readiness & Team Support", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "စာရင်းစစ်ဆေးရာတွင် အမှားတွေ့ရှိမှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Flexibility", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "AP/AR Accountant KPI", "pos": "AP/AR Accountant", "region": "MDY Office", "lvl": "L2", "cats": [
                {"name": "Accounts Payable (AP) Control", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ပေးရန်ကျန်စာရင်း အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Accounts Receivable (AR) Control", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ရရန်ကျန်စာရင်း အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Compliance, Policy Adherence & Security", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "စည်းကမ်းဖောက်ဖျက်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Variance Reporting & Stakeholder Support", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Report နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Support", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Stock Accountant KPI (MDY)", "pos": "Stock Accountant", "region": "MDY Office", "lvl": "L2", "cats": [
                {"name": "Inventory Control & Ledger Accuracy", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": "ပစ္စည်းစာရင်းကွာဟမှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Costing Accuracy & Variance Analysis", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "တန်ဖိုးတွက်ချက်မှု အမှား", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Stock Reconciliation & Risk Management", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Reconciliation လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Report နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Flexibility", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Stock Accountant KPI (YGN)", "pos": "Stock Accountant", "region": "YGN Office", "lvl": "L2", "cats": [
                {"name": "Inventory Control & Ledger Accuracy", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": "ပစ္စည်းစာရင်းကွာဟမှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Costing Accuracy & Variance Analysis", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "တန်ဖိုးတွက်ချက်မှု အမှား", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Stock Reconciliation & Risk Management", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Reconciliation လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Report နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Flexibility", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Senior Accountant KPI", "pos": SENIOR_ACCOUNTANT_POSITION, "region": "YGN Office", "lvl": "L1", "cats": [
                {"name": "Financial Reporting & GL Accuracy", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "GL Report အမှား", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Internal Control & Budget Management", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "ဘတ်ဂျက်လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cost Accounting & Inventory Oversight", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Costing အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Leadership & Team Accountability", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Team စီမံခန့်ခွဲမှု အားနည်းခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Audit Readiness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Compliance လိုက်နာမှု အားနည်းခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Attendance, Punctuality & Flexibility", "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]}
        ]

        formulas_to_add = []
        rules_to_add = [] 
        
        # Keep track of created items for Assignment Phase
        created_formula_maps = {} 

        for f in formulas:
            with db.no_autoflush:
                # 🌟 Database ဆက်စပ်မှုအတွက် Position ကို သေချာစွာ ရှာဖွေ/တည်ဆောက်မည်
                stmt_pos = select(Position).where(Position.name == f["pos"])
                pos_obj = (await db.execute(stmt_pos)).scalars().first()
                if not pos_obj:
                    pos_obj = Position(name=f["pos"], department_id=finance_dept_id)
                    db.add(pos_obj)
                    await db.commit()
                    await db.refresh(pos_obj)

                stmt = select(KPIFormula).where(
                    KPIFormula.department_id == finance_dept_id,
                    KPIFormula.position_name == f["pos"],
                    KPIFormula.region_town == f["region"]
                )
                exists = (await db.execute(stmt)).scalars().first()

                if not exists:
                    new_f = KPIFormula()
                    new_f.id = uuid.uuid4()
                    new_f.name = f["formula_title"]
                    new_f.description = f"KPI formula for {f['pos']} in {f['region']}"
                    new_f.expression = None
                    new_f.department_id = finance_dept_id
                    new_f.position_name = f["pos"]
                    new_f.position_id = pos_obj.id  # 🌟 Linked to actual Position table
                    new_f.region_town = f["region"]
                    new_f.user_level = f["lvl"]
                    new_f.categories = f["cats"]
                    new_f.status = FormulaStatus.ACTIVE
                    new_f.scope = FormulaScope.DEPARTMENT
                    new_f.is_active = True
                    new_f.version = 1
                    new_f.created_by_id = sa_user.id if sa_user else None
                    
                    # Upsert KPI categories (create if not exists)
                    for cat in f["cats"]:
                        cat_name = cat.get("name")
                        if cat_name:
                            stmt_cat = select(KpiCategory).where(KpiCategory.name == cat_name)
                            existing_cat = (await db.execute(stmt_cat)).scalar_one_or_none()
                            if not existing_cat:
                                new_cat = KpiCategory(name=cat_name)
                                db.add(new_cat)
                                await db.commit()
                                await db.refresh(new_cat)
                            # Create or ensure KpiRule entry for this category
                            stmt_rule = select(KpiRule).where(
                                KpiRule.department_id == finance_dept_id,
                                KpiRule.position_id == pos_obj.id,
                                KpiRule.level == f["lvl"],
                                KpiRule.category_name == cat_name,
                            )
                            existing_rule = (await db.execute(stmt_rule)).scalar_one_or_none()
                            if not existing_rule:
                                new_rule = KpiRule(
                                    department_id=finance_dept_id,
                                    position_id=pos_obj.id,
                                    level=f["lvl"],
                                    category_name=cat_name,
                                    weight=cat.get("weight", 0.0),
                                    is_active=True,
                                )
                                db.add(new_rule)
                                await db.commit()
                                await db.refresh(new_rule)
                    
                    formulas_to_add.append(new_f)
                    created_formula_maps[f["formula_title"]] = {"formula_id": new_f.id, "position_id": pos_obj.id}


                    # 🌟 Normalized Scoring Rules များ သေချာ ထည့်သွင်းခြင်း
                    for cat in f["cats"]:
                        cat_name = cat.get("name")
                        for ded in cat.get("deductions", []):
                            rule_name = ded.get("name", "Unknown Rule")
                            measure_type = ded.get("measure_type", "count")
                            for rule in ded.get("rules", []):
                                op = rule.get("operator", "==")
                                val = float(rule.get("value", 0))
                                score = float(rule.get("deduct_score", 0))
                                
                                new_rule_item = KPIScoringRuleItem(
                                    formula_id=new_f.id,
                                    category_name=cat_name,
                                    rule_name=rule_name,
                                    measure_type=measure_type,
                                    operator=op,
                                    target_value=val,
                                    deduct_score=score
                                )
                                rules_to_add.append(new_rule_item)
                                
                    print(f"Prepared Formula & Rules: {f['formula_title']}")
                else:
                    print(f"Skipped: {f['formula_title']} (Already Exists)")
                    created_formula_maps[f["formula_title"]] = {"formula_id": exists.id, "position_id": pos_obj.id}

        # Insert formulas individually to avoid large transaction overload
        for formula in formulas_to_add:
            db.add(formula)
            await db.commit()

        # Insert scoring rules in manageable batches (e.g., 100 at a time)
        batch_size = 100
        for i in range(0, len(rules_to_add), batch_size):
            db.add_all(rules_to_add[i:i + batch_size])
            await db.commit()
            # Redundant batch insertion removed
        # Redundant rule batch insertion removed – rules already inserted in batches above

        # Backfill the canonical category lookup for formulas created by
        # earlier seed versions as well as newly-created formulas.
        all_formulas = (await db.execute(select(KPIFormula))).scalars().all()
        missing_categories = set()
        for formula in all_formulas:
            for category in formula.categories or []:
                category_name = category.get("name", "").strip() if isinstance(category, dict) else ""
                if category_name:
                    missing_categories.add(category_name)
        if missing_categories:
            existing_categories = set(
                (
                    await db.execute(
                        select(KpiCategory.name).where(KpiCategory.name.in_(missing_categories))
                    )
                ).scalars().all()
            )
            db.add_all(
                KpiCategory(name=name)
                for name in sorted(missing_categories - existing_categories)
            )
            await db.commit()

        # 🌟 6. Link Everything Together (User -> Assignment -> Formula -> Period) 🌟
        print("Finalizing KPI Assignments linking...")
        
        test_email = "cashier.mdy@kpi.com"
        demo_password = (
            resolve_seed_password("SEED_TEST_L2_PASSWORD")
            if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False)
            else None
        ) or resolve_user_password(test_email)
        stmt = select(User).where(User.email == test_email)
        test_user = (await db.execute(stmt)).scalars().first()
        
        if "Cashier KPI (MDY)" in created_formula_maps:
            f_map = created_formula_maps["Cashier KPI (MDY)"]

            if not test_user and demo_password:
                test_user = User(
                    email=test_email,
                    first_name="Aung",
                    last_name="Cashier",
                    position_id=f_map["position_id"],
                    hashed_password=get_password_hash(demo_password),
                    role_id=role_dict["Department User"].id,
                    is_active=True,
                    must_change_password=False,
                )
                db.add(test_user)
                await db.commit()
                await db.refresh(test_user)

                db.add(UserDepartment(user_id=test_user.id, department_id=finance_dept_id, user_level="L2"))
                await db.commit()
            elif test_user and demo_password:
                test_user.hashed_password = get_password_hash(demo_password)
                test_user.is_active = True
                test_user.failed_login_attempts = 0
                test_user.is_locked = False
                test_user.lockout_until = None
                test_user.must_change_password = False
                await db.commit()

            if test_user and period and sa_user:
                existing_assignment = (await db.execute(
                    select(KPIAssignment).where(
                        KPIAssignment.user_id == test_user.id,
                        KPIAssignment.kpi_formula_id == f_map["formula_id"],
                        KPIAssignment.kpi_period_id == period.id,
                        KPIAssignment.is_deleted.is_(False),
                    )
                )).scalars().first()
                if not existing_assignment:
                    db.add(KPIAssignment(
                        department_id=finance_dept_id,
                        position_id=f_map["position_id"],
                        user_id=test_user.id,
                        kpi_formula_id=f_map["formula_id"],
                        kpi_period_id=period.id,
                        assigned_by_id=sa_user.id,
                        assigned_at=datetime.now(UTC).replace(tzinfo=None),
                    ))
                    await db.commit()

        position_dict = {}
        finance_positions = (await db.execute(
            select(Position).where(Position.name.in_(["Accountant", SENIOR_ACCOUNTANT_POSITION]))
        )).scalars().all()
        for position in finance_positions:
            position_dict[position.name] = position

        if "Accountant" not in position_dict:
            accountant_position = Position(name="Accountant", department_id=finance_dept_id)
            db.add(accountant_position)
            await db.commit()
            await db.refresh(accountant_position)
            position_dict[accountant_position.name] = accountant_position
        if SENIOR_ACCOUNTANT_POSITION not in position_dict:
            senior_accountant_position = Position(
                name=SENIOR_ACCOUNTANT_POSITION, department_id=finance_dept_id
            )
            db.add(senior_accountant_position)
            await db.commit()
            await db.refresh(senior_accountant_position)
            position_dict[senior_accountant_position.name] = senior_accountant_position

        async def _ensure_finance_user(email: str, last_name: str, user_level: str):
            existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
            password = (
                resolve_seed_password(f"SEED_TEST_{user_level}_PASSWORD")
                if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False)
                else None
            ) or resolve_user_password(email)
            position = position_dict[
                SENIOR_ACCOUNTANT_POSITION if user_level == "L1" else "Accountant"
            ]

            if existing:
                if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False) and password:
                    existing.hashed_password = get_password_hash(password)
                    existing.is_active = True
                    existing.is_locked = False
                    existing.failed_login_attempts = 0
                    existing.lockout_until = None
                    existing.must_change_password = False
                existing.position_id = position.id
                dept_link = (await db.execute(
                    select(UserDepartment).where(
                        UserDepartment.user_id == existing.id,
                        UserDepartment.department_id == finance_dept_id,
                    )
                )).scalars().first()
                if not dept_link:
                    db.add(UserDepartment(user_id=existing.id, department_id=finance_dept_id, user_level=user_level))
                elif dept_link.user_level != user_level:
                    dept_link.user_level = user_level
                await db.commit()
                return existing

            if not password:
                return None
            user = User(
                email=email,
                first_name="Finance",
                last_name=last_name,
                position_id=position.id,
                hashed_password=get_password_hash(password),
                role_id=role_dict["Department User"].id,
                is_active=True,
                must_change_password=False,
            )
            db.add(user)
            await db.commit()
            await db.refresh(user)
            db.add(UserDepartment(user_id=user.id, department_id=finance_dept_id, user_level=user_level))
            await db.commit()
            return user

        await _ensure_finance_user("finance.l0@kpi.com", "L0", "L0")
        await _ensure_finance_user("finance.l1@kpi.com", "L1", "L1")

        async def _ensure_local_test_account(
            account: tuple[str, str] | None,
            role_name: str,
            first_name: str,
            last_name: str,
            *,
            must_change_password: bool,
            department_id=None,
            position=None,
            user_level: str | None = None,
        ):
            if not account:
                return None
            email, password = account
            existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
            if existing is None:
                existing = User(
                    email=email,
                    first_name=first_name,
                    last_name=last_name,
                    hashed_password=get_password_hash(password),
                    role_id=role_dict[role_name].id,
                    position_id=position.id if position else None,
                    is_active=True,
                    must_change_password=must_change_password,
                )
                db.add(existing)
                await db.commit()
                await db.refresh(existing)
            else:
                existing.hashed_password = get_password_hash(password)
                existing.role_id = role_dict[role_name].id
                existing.position_id = position.id if position else None
                existing.is_active = True
                existing.is_locked = False
                existing.failed_login_attempts = 0
                existing.lockout_until = None
                existing.must_change_password = must_change_password
                await db.commit()

            if department_id and user_level:
                dept_link = (await db.execute(
                    select(UserDepartment).where(
                        UserDepartment.user_id == existing.id,
                        UserDepartment.department_id == department_id,
                    )
                )).scalars().first()
                if dept_link is None:
                    db.add(UserDepartment(
                        user_id=existing.id,
                        department_id=department_id,
                        user_level=user_level,
                    ))
                else:
                    dept_link.user_level = user_level
                await db.commit()
            return existing

        if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False):
            await _ensure_local_test_account(
                resolve_test_account("SUPERADMIN"),
                "Super Admin",
                "Phase 2.5",
                "Super Admin",
                must_change_password=False,
            )
            await _ensure_local_test_account(
                resolve_test_account("ADMIN"),
                "Admin",
                "Phase 2.5",
                "Admin",
                must_change_password=False,
                department_id=finance_dept_id,
                position=position_dict["Accountant"],
                user_level="L0",
            )
            await _ensure_local_test_account(
                resolve_test_account("FIRST_LOGIN"),
                "Department User",
                "Phase 2.5",
                "First Login",
                must_change_password=True,
                department_id=finance_dept_id,
                position=position_dict["Accountant"],
                user_level="L2",
            )
            print("Optional local Phase 2.5 test accounts synchronized.")

        print("KPI assignment and department user seed complete.")
        print("✅ Database seeding completed successfully! All rules and relations are perfectly configured.")


async def run_seed():
    """Run the complete seed safely when multiple workers start together."""
    async with AsyncSessionLocal() as lock_db:
        is_postgresql = lock_db.get_bind().dialect.name == "postgresql"
        if is_postgresql:
            await lock_db.execute(
                text("SELECT pg_advisory_lock(:lock_key)"),
                {"lock_key": SEED_ADVISORY_LOCK_KEY},
            )
        try:
            await _run_seed()
        finally:
            if is_postgresql:
                await lock_db.execute(
                    text("SELECT pg_advisory_unlock(:lock_key)"),
                    {"lock_key": SEED_ADVISORY_LOCK_KEY},
                )


if __name__ == "__main__":
    asyncio.run(run_seed())
