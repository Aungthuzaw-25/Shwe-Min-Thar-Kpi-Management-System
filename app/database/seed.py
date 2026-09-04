import asyncio
import sys
import os
import uuid
import calendar
from datetime import datetime, timezone

# Project Root Directory ကို System Path ထဲထည့်ရန်
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))

from sqlalchemy.future import select
from app.database.connection import AsyncSessionLocal
from app.modules.users.models import User, Position, Role, Permission, RolePermission
from app.core.security import get_password_hash
from app.modules.departments.models import Department, UserDepartment

# 🌟 Import ALL KPI Models correctly for Fully Relational Ecosystem
from app.modules.kpi.models import (
    KPIFormula, FormulaStatus, FormulaScope, KPIScoringRuleItem, 
    KPIPeriod, KPIPeriodStatus, KPIAssignment, KpiCategory, KpiRule
)

ROLES = [
    {"name": "Super Admin", "description": "Has full system control"},
    {"name": "System Super Admin", "description": "System-wide administration"},
    {"name": "Admin", "description": "Manages assigned organization"},
    {"name": "HR", "description": "Human resources administration"},
    {"name": "Department User", "description": "Operational users (L0, L1, L2)"}
]


def resolve_seed_password(*candidate_names: str) -> str | None:
    for name in candidate_names:
        value = os.getenv(name)
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

async def run_seed():
    async with AsyncSessionLocal() as db:
        print("Starting database seed...")

        for code, description in PERMISSION_CATALOG:
            existing_permission = (await db.execute(select(Permission).where(Permission.code == code))).scalar_one_or_none()
            if not existing_permission:
                db.add(Permission(code=code, description=description))
        await db.commit()
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

        # 2. Roles
        role_dict = {}
        for role_data in ROLES:
            stmt = select(Role).where(Role.name == role_data["name"])
            role = (await db.execute(stmt)).scalar_one_or_none()
            if not role:
                role = Role(**role_data)
                db.add(role)
                await db.commit()
                await db.refresh(role)
            role_dict[role.name] = role

        # Seed safe, useful defaults only for roles that have no customized
        # matrix yet.  Existing role assignments are never overwritten.
        all_codes = {code for code, _ in PERMISSION_CATALOG}
        role_defaults = {
            "Super Admin": all_codes,
            "System Super Admin": all_codes,
            "Admin": all_codes - {"system.settings.edit", "system.audit.view"},
            "HR": {
                "users.view", "users.create", "users.edit", "users.activate",
                "users.deactivate", "users.password_reset", "department.view",
                "kpi.view", "report.view", "report.view_all",
                "system.permissions.manage",
            },
            "Department User": {
                "users.view", "tasks.view", "tasks.create", "tasks.edit",
                "tasks.delete", "tasks.submit", "tasks.resubmit",
                "kpi.view", "report.view", "department.view",
            },
        }
        for role_name, codes in role_defaults.items():
            role = role_dict[role_name]
            link_count = (await db.execute(
                select(RolePermission).where(RolePermission.role_id == role.id)
            )).scalars().all()
            if not link_count:
                permission_rows = (await db.execute(
                    select(Permission).where(Permission.code.in_(codes))
                )).scalars().all()
                for permission in permission_rows:
                    db.add(RolePermission(
                        role_id=role.id, permission_id=permission.id
                    ))
        await db.commit()

        # 3. Super Admin
        sa_email = "superadmin@kpi.com"
        stmt = select(User).where(User.email == sa_email)
        sa_user = (await db.execute(stmt)).scalar_one_or_none()
        
        bootstrap_password = resolve_seed_password(
            "SEED_SUPER_ADMIN_PASSWORD",
            "UI_TEST_SUPERADMIN_PASSWORD",
        )
        if not sa_user:
            if bootstrap_password:
                sa_user = User(
                    email=sa_email, first_name="System", last_name="Super Admin",
                    hashed_password=get_password_hash(bootstrap_password),
                    role_id=role_dict["Super Admin"].id, is_active=True, must_change_password=False
                )
                db.add(sa_user)
                await db.commit()
                await db.refresh(sa_user)
                print("Super Admin created!")
            else:
                print("Skipping Super Admin bootstrap: no seed password was configured.")
        elif bootstrap_password:
            sa_user.hashed_password = get_password_hash(bootstrap_password)
            sa_user.is_active = True
            sa_user.failed_login_attempts = 0
            sa_user.is_locked = False
            sa_user.lockout_until = None
            sa_user.must_change_password = False
            await db.commit()
            print("Super Admin credentials refreshed from seed configuration.")
        else:
            print("Super Admin already exists; preserving its credential and account state.")

        # 🌟 4. KPI Period Setup (Active Cycle) 🌟
        target_date = datetime.now(timezone.utc)
        year, month = target_date.year, target_date.month
        period_start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
        last_day = calendar.monthrange(year, month)[1]
        period_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
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
            {"formula_title": "Senior Accountant KPI", "pos": "Senior Accountant", "region": "YGN Office", "lvl": "L1", "cats": [
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
                stmt_pos = select(Position).where(
                    Position.name == f["pos"], 
                    Position.department_id == finance_dept_id
                )
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
                    new_f.created_by_id = sa_user.id
                    
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

        # 🌟 6. Link Everything Together (User -> Assignment -> Formula -> Period) 🌟
        print("Finalizing KPI Assignments linking...")
        
        test_email = "cashier.mdy@kpi.com"
        demo_password = resolve_user_password(test_email)
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
                        KPIAssignment.is_deleted == False,
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
                        assigned_at=datetime.now(timezone.utc).replace(tzinfo=None),
                    ))
                    await db.commit()

        position_dict = {}
        finance_positions = (await db.execute(
            select(Position).where(Position.department_id == finance_dept_id)
        )).scalars().all()
        for position in finance_positions:
            position_dict[position.name] = position

        if "Accountant" not in position_dict:
            accountant_position = Position(name="Accountant", department_id=finance_dept_id)
            db.add(accountant_position)
            await db.commit()
            await db.refresh(accountant_position)
            position_dict[accountant_position.name] = accountant_position

        async def _ensure_finance_user(email: str, last_name: str, user_level: str):
            existing = (await db.execute(select(User).where(User.email == email))).scalars().first()
            password = resolve_user_password(email)

            if existing:
                if password:
                    existing.hashed_password = get_password_hash(password)
                    existing.is_active = True
                    existing.failed_login_attempts = 0
                    existing.is_locked = False
                    existing.lockout_until = None
                    existing.must_change_password = False
                    if not existing.position_id and position_dict.get("Accountant"):
                        existing.position_id = position_dict["Accountant"].id
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
                position_id=position_dict.get("Accountant").id,
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

        print("KPI assignment and department user seed complete.")
        print("✅ Database seeding completed successfully! All rules and relations are perfectly configured.")

if __name__ == "__main__":
    asyncio.run(run_seed())
