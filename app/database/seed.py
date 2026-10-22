import asyncio
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
from app.core.security import get_password_hash_async
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
    PeriodType,
)
from app.modules.kpi.period_ranges import calendar_period_range
from app.modules.users.models import Permission, Position, Role, RolePermission, User

SUPER_ADMIN_ROLE = "Super Admin"
DEPARTMENT_USER_ROLE = "Department User"
USERS_VIEW_PERMISSION = "users.view"
KPI_VIEW_PERMISSION = "kpi.view"
REPORT_VIEW_PERMISSION = "report.view"
DEPARTMENT_VIEW_PERMISSION = "department.view"
MDY_OFFICE = "MDY Office"
YGN_OFFICE = "YGN Office"
CASHIER_KPI_MDY = "Cashier KPI (MDY)"
ACCOUNTING_ERROR_DEDUCTION = "စာရင်းလွဲချော်မှု"
DISCIPLINE_VIOLATION_DEDUCTION = "စည်းကမ်းဖောက်ဖျက်မှု"
REPORT_LATE_DEDUCTION = "Report နောက်ကျခြင်း"
ATTENDANCE_SUPPORT_CATEGORY = "Attendance, Punctuality & Support"
ATTENDANCE_FLEXIBILITY_CATEGORY = "Attendance, Punctuality & Flexibility"
PHASE_2_5 = "Phase 2.5"
SEED_CASHIER_MDY_EMAIL = "cashier.mdy@kpi.com"

ROLES = [
    {"name": SUPER_ADMIN_ROLE, "description": "Has full system control"},
    {"name": "Admin", "description": "Manages assigned organization"},
    {"name": "HR", "description": "Human resources administration"},
    {"name": DEPARTMENT_USER_ROLE, "description": "Operational users (L0, L1, L2)"}
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
        SEED_CASHIER_MDY_EMAIL: (
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
    (USERS_VIEW_PERMISSION, "View users"), ("users.create", "Create users"), ("users.edit", "Edit users"),
    ("users.delete", "Delete users"), ("users.activate", "Activate users"), ("users.deactivate", "Deactivate users"),
    ("users.password_reset", "Reset user passwords"),
    ("tasks.view", "View tasks"), ("tasks.create", "Create tasks"), ("tasks.edit", "Edit tasks"),
    ("tasks.delete", "Delete tasks"), ("tasks.submit", "Submit tasks"), ("tasks.resubmit", "Resubmit tasks"), ("tasks.approve", "Approve tasks"),
    ("tasks.reject", "Reject tasks"), ("tasks.return", "Return tasks"),
    (KPI_VIEW_PERMISSION, "View KPIs"), ("kpi.create", "Create KPIs"), ("kpi.edit", "Edit KPIs"),
    ("kpi.delete", "Delete KPIs"), ("kpi.assign", "Assign KPIs"), ("kpi.calculate", "Calculate KPIs"),
    ("kpi.rule.view", "View KPI rules"), ("kpi.rule.create", "Create KPI rules"),
    ("kpi.rule.edit", "Edit KPI rules"), ("kpi.rule.delete", "Delete KPI rules"),
    (REPORT_VIEW_PERMISSION, "View reports"), ("report.create", "Create reports"), ("report.export", "Export reports"),
    ("report.view_all", "View other users' reports"),
    (DEPARTMENT_VIEW_PERMISSION, "View departments"), ("department.create", "Create departments"),
    ("department.edit", "Edit departments"), ("department.delete", "Delete departments"),
    ("system.settings.view", "View settings"), ("system.settings.edit", "Edit settings"),
    ("system.audit.view", "View audit history"),
    ("system.permissions.manage", "Manage role permissions and user overrides"),
]

ROLE_DEFAULTS = {
    SUPER_ADMIN_ROLE: {code for code, _ in PERMISSION_CATALOG},
    "Admin": {code for code, _ in PERMISSION_CATALOG}
    - {"system.settings.edit", "system.audit.view"},
    "HR": {
        USERS_VIEW_PERMISSION, "users.create", "users.edit", "users.activate",
        "users.deactivate", "users.password_reset", DEPARTMENT_VIEW_PERMISSION,
        KPI_VIEW_PERMISSION, REPORT_VIEW_PERMISSION, "report.view_all",
        "system.permissions.manage",
    },
    DEPARTMENT_USER_ROLE: {
        USERS_VIEW_PERMISSION, "tasks.view", "tasks.create", "tasks.edit",
        "tasks.delete", "tasks.submit", "tasks.resubmit",
        "tasks.approve", "tasks.reject", "tasks.return",
        KPI_VIEW_PERMISSION, REPORT_VIEW_PERMISSION, DEPARTMENT_VIEW_PERMISSION,
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
    new_permissions = [
        Permission(code=code, description=description)
        for code, description in PERMISSION_CATALOG
        if code not in permissions_by_code
    ]
    db.add_all(new_permissions)
    if new_permissions:
        await db.flush()
        permissions_by_code.update(
            (permission.code, permission) for permission in new_permissions
        )

    role_names = [role_data["name"] for role_data in ROLES]
    existing_roles = (
        await db.execute(select(Role).where(Role.name.in_(role_names)))
    ).scalars().all()
    roles_by_name = {role.name: role for role in existing_roles}
    new_roles = [
        Role(**role_data)
        for role_data in ROLES
        if role_data["name"] not in roles_by_name
    ]
    db.add_all(new_roles)
    if new_roles:
        await db.flush()
        roles_by_name.update((role.name, role) for role in new_roles)

    role_ids = [role.id for role in roles_by_name.values()]
    existing_links = (
        await db.execute(
            select(RolePermission.role_id, RolePermission.permission_id).where(
                RolePermission.role_id.in_(role_ids)
            )
        )
    ).all()
    permission_ids_by_role = {}
    for role_id, permission_id in existing_links:
        permission_ids_by_role.setdefault(role_id, set()).add(permission_id)
    new_links = []
    for role_name, codes in ROLE_DEFAULTS.items():
        role = roles_by_name[role_name]
        existing_permission_ids = permission_ids_by_role.get(role.id, set())
        for code in codes:
            permission = permissions_by_code[code]
            if permission.id not in existing_permission_ids:
                new_links.append(
                    RolePermission(role_id=role.id, permission_id=permission.id)
                )
                existing_permission_ids.add(permission.id)
    db.add_all(new_links)
    await db.flush()
    return roles_by_name


async def _ensure_super_admin(db, role_id):
    """Create the first Super Admin without changing an existing account."""
    existing_super_admin = (
        await db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(Role.name == SUPER_ADMIN_ROLE)
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
            last_name=SUPER_ADMIN_ROLE,
            hashed_password=await get_password_hash_async(bootstrap_password),
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
    department_names = [department["name"] for department in departments]
    existing_departments = (
        await db.execute(
            select(Department).where(Department.name.in_(department_names))
        )
    ).scalars().all()
    dept_dict = {department.name: department for department in existing_departments}
    new_departments = [
        Department(**department)
        for department in departments
        if department["name"] not in dept_dict
    ]
    db.add_all(new_departments)
    if new_departments:
        await db.flush()
        dept_dict.update(
            (department.name, department) for department in new_departments
        )
        await db.commit()
    print("Departments seeded!")
    return dept_dict


async def _find_active_super_admin(db):
    sa_user = (
        await db.execute(
            select(User)
            .join(Role, User.role_id == Role.id)
            .where(
                Role.name == SUPER_ADMIN_ROLE,
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


async def _ensure_active_period(db):
    target_date = datetime.now(UTC)
    period_range = calendar_period_range(PeriodType.MONTHLY, target_date.date())
    period_label = target_date.strftime("%B %Y")
    period = (
        await db.execute(select(KPIPeriod).where(KPIPeriod.label == period_label))
    ).scalars().first()
    if not period:
        period = KPIPeriod(
            label=period_label,
            start_date=period_range.start_inclusive,
            end_date=period_range.end_exclusive,
            status=KPIPeriodStatus.OPEN,
        )
        db.add(period)
        await db.commit()
        await db.refresh(period)
        print(f"Active KPI Period '{period_label}' created!")
    return period


async def _ensure_formula_position(db, formula, department_id):
    position = (
        await db.execute(select(Position).where(Position.name == formula["pos"]))
    ).scalars().first()
    if position:
        return position
    position = Position(name=formula["pos"], department_id=department_id)
    db.add(position)
    await db.commit()
    await db.refresh(position)
    return position


async def _ensure_formula_category_rule(db, category, formula, department_id, position_id):
    category_name = category.get("name")
    if not category_name:
        return
    existing_category = (
        await db.execute(
            select(KpiCategory).where(KpiCategory.name == category_name)
        )
    ).scalar_one_or_none()
    if not existing_category:
        new_category = KpiCategory(name=category_name)
        db.add(new_category)
        await db.commit()
        await db.refresh(new_category)

    existing_rule = (
        await db.execute(
            select(KpiRule).where(
                KpiRule.department_id == department_id,
                KpiRule.position_id == position_id,
                KpiRule.level == formula["lvl"],
                KpiRule.category_name == category_name,
            )
        )
    ).scalar_one_or_none()
    if existing_rule:
        return
    new_rule = KpiRule(
        department_id=department_id,
        position_id=position_id,
        level=formula["lvl"],
        category_name=category_name,
        weight=category.get("weight", 0.0),
        is_active=True,
    )
    db.add(new_rule)
    await db.commit()
    await db.refresh(new_rule)


def _scoring_items_for_formula(formula_id, categories):
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


async def _prepare_formula(db, formula, department_id, creator_id):
    position = await _ensure_formula_position(db, formula, department_id)
    existing = (
        await db.execute(
            select(KPIFormula).where(
                KPIFormula.department_id == department_id,
                KPIFormula.position_name == formula["pos"],
                KPIFormula.region_town == formula["region"],
            )
        )
    ).scalars().first()
    if existing:
        print(f"Skipped: {formula['formula_title']} (Already Exists)")
        return None, [], {
            "formula_id": existing.id,
            "position_id": position.id,
        }

    new_formula = KPIFormula(
        id=uuid.uuid4(),
        name=formula["formula_title"],
        description=f"KPI formula for {formula['pos']} in {formula['region']}",
        expression=None,
        department_id=department_id,
        position_name=formula["pos"],
        position_id=position.id,
        region_town=formula["region"],
        user_level=formula["lvl"],
        categories=formula["cats"],
        status=FormulaStatus.ACTIVE,
        scope=FormulaScope.DEPARTMENT,
        is_active=True,
        version=1,
        created_by_id=creator_id,
    )
    for category in formula["cats"]:
        await _ensure_formula_category_rule(
            db, category, formula, department_id, position.id
        )
    print(f"Prepared Formula & Rules: {formula['formula_title']}")
    return (
        new_formula,
        _scoring_items_for_formula(new_formula.id, formula["cats"]),
        {"formula_id": new_formula.id, "position_id": position.id},
    )


async def _backfill_kpi_categories(db):
    all_formulas = (await db.execute(select(KPIFormula))).scalars().all()
    missing_categories = {
        category.get("name", "").strip()
        for formula in all_formulas
        for category in formula.categories or []
        if isinstance(category, dict) and category.get("name", "").strip()
    }
    if not missing_categories:
        return
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


async def _seed_formula_records(db, formulas, department_id, creator_id):
    formulas_to_add = []
    rules_to_add = []
    created_formula_maps = {}
    for formula in formulas:
        with db.no_autoflush:
            new_formula, rule_items, formula_map = await _prepare_formula(
                db, formula, department_id, creator_id
            )
        if new_formula is not None:
            formulas_to_add.append(new_formula)
            rules_to_add.extend(rule_items)
        created_formula_maps[formula["formula_title"]] = formula_map

    for formula in formulas_to_add:
        db.add(formula)
        await db.commit()
    for offset in range(0, len(rules_to_add), 100):
        db.add_all(rules_to_add[offset : offset + 100])
        await db.commit()
    await _backfill_kpi_categories(db)
    return created_formula_maps


async def _seed_cashier_assignment(
    db, created_formula_maps, role_dict, department_id, period, sa_user
):
    test_email = SEED_CASHIER_MDY_EMAIL
    demo_password = (
        resolve_seed_password("SEED_TEST_L2_PASSWORD")
        if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False)
        else None
    ) or resolve_user_password(test_email)
    test_user = (
        await db.execute(select(User).where(User.email == test_email))
    ).scalars().first()
    formula_map = created_formula_maps.get(CASHIER_KPI_MDY)
    if not formula_map:
        return
    test_user = await _synchronize_cashier_user(
        db, test_user, demo_password, formula_map, role_dict, department_id
    )
    if test_user and period and sa_user:
        await _assign_cashier_formula(
            db, test_user, formula_map, department_id, period, sa_user
        )


async def _synchronize_cashier_user(
    db, test_user, demo_password, formula_map, role_dict, department_id
):
    if not test_user and demo_password:
        test_user = User(
            email=SEED_CASHIER_MDY_EMAIL,
            first_name="Aung",
            last_name="Cashier",
            position_id=formula_map["position_id"],
            hashed_password=await get_password_hash_async(demo_password),
            role_id=role_dict[DEPARTMENT_USER_ROLE].id,
            is_active=True,
            must_change_password=False,
        )
        db.add(test_user)
        await db.commit()
        await db.refresh(test_user)
        db.add(
            UserDepartment(
                user_id=test_user.id, department_id=department_id, user_level="L2"
            )
        )
        await db.commit()
    elif test_user and demo_password:
        test_user.hashed_password = await get_password_hash_async(demo_password)
        test_user.is_active = True
        test_user.failed_login_attempts = 0
        test_user.is_locked = False
        test_user.lockout_until = None
        test_user.must_change_password = False
        await db.commit()
    return test_user


async def _assign_cashier_formula(db, test_user, formula_map, department_id, period, sa_user):
    existing_assignment = (
        await db.execute(
            select(KPIAssignment).where(
                KPIAssignment.user_id == test_user.id,
                KPIAssignment.kpi_formula_id == formula_map["formula_id"],
                KPIAssignment.kpi_period_id == period.id,
                KPIAssignment.is_deleted.is_(False),
            )
        )
    ).scalars().first()
    if existing_assignment:
        return
    db.add(
        KPIAssignment(
            department_id=department_id,
            position_id=formula_map["position_id"],
            user_id=test_user.id,
            kpi_formula_id=formula_map["formula_id"],
            kpi_period_id=period.id,
            assigned_by_id=sa_user.id,
            assigned_at=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    await db.commit()


async def _ensure_finance_positions(db, department_id):
    positions = (
        await db.execute(
            select(Position).where(
                Position.name.in_(["Accountant", SENIOR_ACCOUNTANT_POSITION])
            )
        )
    ).scalars().all()
    position_dict = {position.name: position for position in positions}
    for name in ("Accountant", SENIOR_ACCOUNTANT_POSITION):
        if name in position_dict:
            continue
        position = Position(name=name, department_id=department_id)
        db.add(position)
        await db.commit()
        await db.refresh(position)
        position_dict[position.name] = position
    return position_dict


async def _ensure_finance_user(
    db, email, last_name, user_level, department_id, role_dict, position_dict
):
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalars().first()
    password = (
        resolve_seed_password(f"SEED_TEST_{user_level}_PASSWORD")
        if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False)
        else None
    ) or resolve_user_password(email)
    position = position_dict[
        SENIOR_ACCOUNTANT_POSITION if user_level == "L1" else "Accountant"
    ]
    if existing:
        return await _update_finance_user(
            db, existing, password, position, department_id, user_level
        )
    if not password:
        return None
    user = User(
        email=email,
        first_name="Finance",
        last_name=last_name,
        position_id=position.id,
        hashed_password=await get_password_hash_async(password),
        role_id=role_dict[DEPARTMENT_USER_ROLE].id,
        is_active=True,
        must_change_password=False,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    db.add(UserDepartment(user_id=user.id, department_id=department_id, user_level=user_level))
    await db.commit()
    return user


async def _update_finance_user(db, user, password, position, department_id, user_level):
    if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False) and password:
        user.hashed_password = await get_password_hash_async(password)
        user.is_active = True
        user.is_locked = False
        user.failed_login_attempts = 0
        user.lockout_until = None
        user.must_change_password = False
    user.position_id = position.id
    department_link = (
        await db.execute(
            select(UserDepartment).where(
                UserDepartment.user_id == user.id,
                UserDepartment.department_id == department_id,
            )
        )
    ).scalars().first()
    if not department_link:
        db.add(
            UserDepartment(
                user_id=user.id, department_id=department_id, user_level=user_level
            )
        )
    elif department_link.user_level != user_level:
        department_link.user_level = user_level
    await db.commit()
    return user


async def _ensure_local_test_account(
    db,
    account,
    role_name,
    first_name,
    last_name,
    role_dict,
    *,
    must_change_password,
    department_id=None,
    position=None,
    user_level=None,
):
    if not account:
        return None
    email, password = account
    existing = (
        await db.execute(select(User).where(User.email == email))
    ).scalars().first()
    if existing is None:
        existing = User(
            email=email,
            first_name=first_name,
            last_name=last_name,
            hashed_password=await get_password_hash_async(password),
            role_id=role_dict[role_name].id,
            position_id=position.id if position else None,
            is_active=True,
            must_change_password=must_change_password,
        )
        db.add(existing)
        await db.commit()
        await db.refresh(existing)
    else:
        await _update_test_account(
            existing, password, role_dict, role_name, must_change_password, position
        )
        await db.commit()
    if department_id and user_level:
        await _ensure_user_department(db, existing, department_id, user_level)
    return existing


async def _update_test_account(user, password, role_dict, role_name, must_change_password, position):
    user.hashed_password = await get_password_hash_async(password)
    user.role_id = role_dict[role_name].id
    user.position_id = position.id if position else None
    user.is_active = True
    user.is_locked = False
    user.failed_login_attempts = 0
    user.lockout_until = None
    user.must_change_password = must_change_password


async def _ensure_user_department(db, user, department_id, user_level):
    department_link = (
        await db.execute(
            select(UserDepartment).where(
                UserDepartment.user_id == user.id,
                UserDepartment.department_id == department_id,
            )
        )
    ).scalars().first()
    if department_link is None:
        db.add(
            UserDepartment(
                user_id=user.id, department_id=department_id, user_level=user_level
            )
        )
    else:
        department_link.user_level = user_level
    await db.commit()


async def _seed_assignment_users(
    db, created_formula_maps, role_dict, department_id, period, sa_user
):
    await _seed_cashier_assignment(
        db, created_formula_maps, role_dict, department_id, period, sa_user
    )
    position_dict = await _ensure_finance_positions(db, department_id)
    await _ensure_finance_user(
        db, "finance.l0@kpi.com", "L0", "L0", department_id, role_dict, position_dict
    )
    await _ensure_finance_user(
        db, "finance.l1@kpi.com", "L1", "L1", department_id, role_dict, position_dict
    )
    if getattr(settings, "SEED_TEST_ACCOUNTS_ENABLED", False):
        shared = {"db": db, "role_dict": role_dict}
        await _ensure_local_test_account(
            account=resolve_test_account("SUPERADMIN"),
            role_name=SUPER_ADMIN_ROLE,
            first_name=PHASE_2_5,
            last_name=SUPER_ADMIN_ROLE,
            must_change_password=False,
            **shared,
        )
        await _ensure_local_test_account(
            account=resolve_test_account("ADMIN"),
            role_name="Admin",
            first_name=PHASE_2_5,
            last_name="Admin",
            must_change_password=False,
            department_id=department_id,
            position=position_dict["Accountant"],
            user_level="L0",
            **shared,
        )
        await _ensure_local_test_account(
            account=resolve_test_account("FIRST_LOGIN"),
            role_name=DEPARTMENT_USER_ROLE,
            first_name=PHASE_2_5,
            last_name="First Login",
            must_change_password=True,
            department_id=department_id,
            position=position_dict["Accountant"],
            user_level="L2",
            **shared,
        )
        print("Optional local Phase 2.5 test accounts synchronized.")
    print("KPI assignment and department user seed complete.")
    print("✅ Database seeding completed successfully! All rules and relations are perfectly configured.")


async def _run_seed():
    async with AsyncSessionLocal() as db:
        print("Starting database seed...")

        await _seed_permissions(db)
        print("Permission catalog seeded!")

        dept_dict = await _seed_departments(db)

        # 2. Roles and permissions
        role_dict = await ensure_roles_and_permissions(db)
        await db.commit()

        # 3. Super Admin bootstrap (explicit seed only; create-only).
        await _ensure_super_admin(db, role_dict[SUPER_ADMIN_ROLE].id)
        # KPI formulas and assignments are authored by the existing internal
        # Super Admin, not by the demo employee used for assignment coverage.
        sa_user = await _find_active_super_admin(db)

        # 🌟 4. KPI Period Setup (Active Cycle) 🌟
        period = await _ensure_active_period(db)

        # 5. KPI Formulas & Scoring Rules Seeding
        print("Starting KPI Formula Seeding...")
        finance_dept_id = dept_dict["Finance & Account"].id
        
        formulas = [
            {"formula_title": CASHIER_KPI_MDY, "pos": "Cashier", "region": MDY_OFFICE, "lvl": "L2", "cats": [
                {"name": "Cash Handling Accuracy & Reconciliation", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": ACCOUNTING_ERROR_DEDUCTION, "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Voucher Management & Ledger Entry", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ဘောက်ချာမှန်ကန်မှု ရာခိုင်နှုန်း", "measure_type": "percentage", "rules": VOUCHER_PERCENTAGE_RULES}]},
                {"name": "Compliance with Financial Policies", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": DISCIPLINE_VIOLATION_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cash Management & External Execution", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "ဘဏ်အပ်နှံမှု နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_SUPPORT_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Cashier KPI (YGN)", "pos": "Cashier", "region": YGN_OFFICE, "lvl": "L2", "cats": [
                {"name": "Cash Handling Accuracy & Reconciliation", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": ACCOUNTING_ERROR_DEDUCTION, "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Voucher Management & Ledger Entry", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ဘောက်ချာမှန်ကန်မှု ရာခိုင်နှုန်း", "measure_type": "percentage", "rules": VOUCHER_PERCENTAGE_RULES}]},
                {"name": "Compliance with Financial Policies", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": DISCIPLINE_VIOLATION_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cash Management & External Execution", "weight": 0.15, "max_score": 5.0, "deductions": [{"name": "ဘဏ်အပ်နှံမှု နောက်ကျခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_SUPPORT_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Cash Accountant KPI", "pos": "Cash Accountant", "region": MDY_OFFICE, "lvl": "L2", "cats": [
                {"name": "Cash & Bank Accuracy & Reconciliation", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": ACCOUNTING_ERROR_DEDUCTION, "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Verification", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": REPORT_LATE_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Compliance, Documentation & Security", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": DISCIPLINE_VIOLATION_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Audit Readiness & Team Support", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "စာရင်းစစ်ဆေးရာတွင် အမှားတွေ့ရှိမှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_FLEXIBILITY_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "AP/AR Accountant KPI", "pos": "AP/AR Accountant", "region": MDY_OFFICE, "lvl": "L2", "cats": [
                {"name": "Accounts Payable (AP) Control", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ပေးရန်ကျန်စာရင်း အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Accounts Receivable (AR) Control", "weight": 0.30, "max_score": 5.0, "deductions": [{"name": "ရရန်ကျန်စာရင်း အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Compliance, Policy Adherence & Security", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": DISCIPLINE_VIOLATION_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Variance Reporting & Stakeholder Support", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": REPORT_LATE_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_SUPPORT_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Stock Accountant KPI (MDY)", "pos": "Stock Accountant", "region": MDY_OFFICE, "lvl": "L2", "cats": [
                {"name": "Inventory Control & Ledger Accuracy", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": "ပစ္စည်းစာရင်းကွာဟမှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Costing Accuracy & Variance Analysis", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "တန်ဖိုးတွက်ချက်မှု အမှား", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Stock Reconciliation & Risk Management", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Reconciliation လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": REPORT_LATE_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_FLEXIBILITY_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Stock Accountant KPI (YGN)", "pos": "Stock Accountant", "region": YGN_OFFICE, "lvl": "L2", "cats": [
                {"name": "Inventory Control & Ledger Accuracy", "weight": 0.35, "max_score": 5.0, "deductions": [{"name": "ပစ္စည်းစာရင်းကွာဟမှု", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Costing Accuracy & Variance Analysis", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "တန်ဖိုးတွက်ချက်မှု အမှား", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Stock Reconciliation & Risk Management", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Reconciliation လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Reporting Timeliness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": REPORT_LATE_DEDUCTION, "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_FLEXIBILITY_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]},
            {"formula_title": "Senior Accountant KPI", "pos": SENIOR_ACCOUNTANT_POSITION, "region": YGN_OFFICE, "lvl": "L1", "cats": [
                {"name": "Financial Reporting & GL Accuracy", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "GL Report အမှား", "measure_type": "count", "rules": STRICT_ERROR_RULES}]},
                {"name": "Internal Control & Budget Management", "weight": 0.25, "max_score": 5.0, "deductions": [{"name": "ဘတ်ဂျက်လွဲချော်မှု", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Cost Accounting & Inventory Oversight", "weight": 0.20, "max_score": 5.0, "deductions": [{"name": "Costing အမှားအယွင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Leadership & Team Accountability", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Team စီမံခန့်ခွဲမှု အားနည်းခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": "Audit Readiness & Compliance", "weight": 0.10, "max_score": 5.0, "deductions": [{"name": "Compliance လိုက်နာမှု အားနည်းခြင်း", "measure_type": "count", "rules": STANDARD_ERROR_RULES}]},
                {"name": ATTENDANCE_FLEXIBILITY_CATEGORY, "weight": 0.10, "max_score": 5.0, "deductions": ATTENDANCE_RULES}
            ]}
        ]

        created_formula_maps = await _seed_formula_records(
            db, formulas, finance_dept_id, sa_user.id if sa_user else None
        )

        print("Finalizing KPI Assignments linking...")
        await _seed_assignment_users(
            db, created_formula_maps, role_dict, finance_dept_id, period, sa_user
        )


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
