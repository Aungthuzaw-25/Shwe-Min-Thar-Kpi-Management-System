import logging
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.kpi import repo as kpi_repo
from app.modules.kpi.engine import KPICalculationEngine, calculate_custom_period_kpi

# Models and utilities
from app.modules.kpi.models import (
    FormulaStatus,
    KpiCategory,
    KPIFormula,
    KPILevelSetting,
    KPIRecord,
    KPIScoringRuleItem,
    PeriodType,
)
from app.modules.kpi.schemas import (
    KPIFormulaCreate,
    KPIFormulaUpdate,
    KPILevelSettingUpdate,
    KpiReportItem,
    KpiReportSummaryResponse,
)
from app.modules.users.models import Position

# Logger for this module
logger = logging.getLogger(__name__)


def _category_name(category) -> str:
    """Return a trimmed category name from a schema object or JSON mapping."""
    value = (
        category.get("name")
        if isinstance(category, dict)
        else getattr(category, "name", None)
    )
    return value.strip() if isinstance(value, str) else ""


async def _sync_formula_categories(db: AsyncSession, categories) -> None:
    """Upsert formula category names into the shared task category lookup."""
    names = {_category_name(category) for category in categories}
    names.discard("")
    if not names:
        return

    existing_names = set(
        (
            await db.execute(
                select(KpiCategory.name).where(KpiCategory.name.in_(names))
            )
        ).scalars().all()
    )
    missing_names = names - existing_names
    if missing_names:
        db.add_all(KpiCategory(name=name) for name in sorted(missing_names))
        await db.flush()


async def _resolve_formula_position(
    db: AsyncSession, department_id: UUID, position_id: UUID | None
) -> Position | None:
    """Resolve an active position only when it belongs to the formula department."""
    if position_id is None:
        return None
    position = await db.get(Position, position_id)
    if (
        position is None
        or position.is_deleted
        or not position.is_active
        or position.department_id != department_id
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Selected position is not active in the formula department.",
        )
    return position


async def sync_existing_formula_categories(db: AsyncSession) -> int:
    """Backfill the shared category lookup from all stored formula JSON payloads."""
    formulas = (await db.execute(select(KPIFormula))).scalars().all()
    for formula in formulas:
        await _sync_formula_categories(db, formula.categories or [])
    await db.commit()
    return len(formulas)

# ==============================================================================
# 1. KPI Level Settings Logic (L0, L1, L2 အဖွင့်အပိတ် ပြုလုပ်ခြင်း)
# ==============================================================================
async def get_kpi_level_settings(db: AsyncSession, department_id: UUID | None = None):
    """လက်ရှိ ဖွင့်/ပိတ် ထားသော Level အားလုံးကို ဆွဲထုတ်မည် (ဌာနအလိုက်ပါ Filter လုပ်နိုင်ရန် ပြင်ဆင်ထားသည်)"""
    stmt = select(KPILevelSetting)
    if department_id:
        stmt = stmt.where(KPILevelSetting.department_id == department_id)
        
    result = await db.execute(stmt)
    return result.scalars().all()

async def update_kpi_level_setting(db: AsyncSession, level_name: str, setting_in: KPILevelSettingUpdate):
    """Level တစ်ခုချင်းစီ ၏ Global (Company Wide) Setting ကို Update လုပ်မည်။"""
    # 🌟 Department ID မပါသော (Global) Setting ကိုသာ ရွေးချယ်ပြင်ဆင်မည်
    stmt = select(KPILevelSetting).where(
        KPILevelSetting.level_name == level_name,
        KPILevelSetting.department_id == None
    )
    result = await db.execute(stmt)
    setting = result.scalars().first()

    if not setting:
        setting = KPILevelSetting(level_name=level_name, is_enabled=setting_in.is_enabled, department_id=None)
        db.add(setting)
    else:
        setting.is_enabled = setting_in.is_enabled

    await db.commit()
    await db.refresh(setting)
    return setting

# ==============================================================================
# 2. KPI Formulas Logic (Admin မှ Category နှင့် Rules များ သတ်မှတ်ခြင်း & Versioning)
# ==============================================================================

async def create_kpi_formula(db: AsyncSession, formula_in: KPIFormulaCreate):
    """KPI Formula အသစ်ဖန်တီးမည် (Region, Position, Categories များပါဝင်သည်)"""
    
    # Payload မှ categories များကို dictionary format သို့ ပြောင်းလဲခြင်း
    categories_data = [cat.model_dump() for cat in formula_in.categories]
    position = await _resolve_formula_position(
        db, formula_in.department_id, formula_in.position_id
    )

    new_formula = KPIFormula(
        name=formula_in.name,
        description=formula_in.description,
        region_town=formula_in.region_town,
        department_id=formula_in.department_id,
        position_id=position.id if position else None,
        position_name=position.name if position else formula_in.position_name,
        user_level=formula_in.user_level,
        categories=categories_data,
        scope=formula_in.scope,
        is_active=formula_in.is_active,
        status=FormulaStatus.DRAFT,
        version=1,
        effective_month=formula_in.effective_month
    )
    db.add(new_formula)
    
    # 🌟 Transaction Integrity: Commit မလုပ်သေးဘဲ Formula ID ရယူရန် flush အသုံးပြုသည်
    await db.flush()

    await _sync_formula_categories(db, formula_in.categories)

    # 🌟 DATA AUTO-SPLITTING: Rule များကို kpi_scoring_rule_items သို့ ခွဲထုတ်ထည့်သွင်းခြင်း
    rules_to_add = []
    for cat in formula_in.categories:
        for ded in cat.deductions:
            for rule in ded.rules:
                op = rule.operator
                val = float(rule.value)
                score = float(rule.deduct_score)
                
                rules_to_add.append(KPIScoringRuleItem(
                    formula_id=new_formula.id,
                    # 🌟 FIX: Standardize names before saving
                    category_name=KPICalculationEngine.standardize_key(cat.name),          
                    rule_name=KPICalculationEngine.standardize_key(ded.name),              
                    measure_type=ded.measure_type,       
                    operator=op,
                    target_value=val,
                    deduct_score=score
                ))
                
    if rules_to_add:
        db.add_all(rules_to_add)

    # အားလုံးအောင်မြင်မှသာ အပြီးသတ် သိမ်းဆည်းမည်
    await db.commit()
    await db.refresh(new_formula)
    return new_formula

async def get_all_kpi_formulas(db: AsyncSession, department_id: UUID = None):
    """Admin များ ကြည့်ရှုရန် Formula အားလုံး (သို့) ဌာနအလိုက် ဆွဲထုတ်မည်"""
    stmt = select(KPIFormula).order_by(KPIFormula.created_at.desc())
    if department_id:
        stmt = stmt.where(KPIFormula.department_id == department_id)
    result = await db.execute(stmt)
    return result.scalars().all()

def _build_scoring_rule_items(formula_id, categories) -> list[KPIScoringRuleItem]:
    """Build KPIScoringRuleItem list from formula categories."""
    rules = []
    for cat in categories:
        for ded in cat.deductions:
            for rule in ded.rules:
                rules.append(KPIScoringRuleItem(
                    formula_id=formula_id,
                    category_name=KPICalculationEngine.standardize_key(cat.name),
                    rule_name=KPICalculationEngine.standardize_key(ded.name),
                    measure_type=ded.measure_type,
                    operator=rule.operator,
                    target_value=float(rule.value),
                    deduct_score=float(rule.deduct_score),
                ))
    return rules


async def _create_new_formula_version(
    db: AsyncSession, formula: KPIFormula, update_data: dict, formula_in: KPIFormulaUpdate
) -> KPIFormula:
    """Create a new DRAFT version of an existing formula."""
    new_categories = formula.categories
    if "categories" in update_data:
        new_categories = list(update_data["categories"])

    new_formula = KPIFormula(
        name=update_data.get("name", formula.name),
        description=update_data.get("description", formula.description),
        region_town=formula.region_town,
        department_id=formula.department_id,
        position_id=update_data.get("position_id", formula.position_id),
        position_name=update_data.get("position_name", formula.position_name),
        user_level=formula.user_level,
        categories=new_categories,
        scope=formula.scope,
        is_active=update_data.get("is_active", True),
        version=formula.version + 1,            # 🌟 Version 1 ဆင့်တက်မည်
        status=FormulaStatus.DRAFT,             # 🌟 DRAFT သို့ ပြန်ရောက်မည်
        parent_formula_id=formula.id,           # 🌟 မူရင်း Formula ကို မှတ်သားထားမည်
    )
    db.add(new_formula)
    await db.flush()

    await _sync_formula_categories(db, formula_in.categories)

    if formula_in.categories:
        rules_to_add = _build_scoring_rule_items(new_formula.id, formula_in.categories)
        if rules_to_add:
            db.add_all(rules_to_add)

    await db.commit()
    await db.refresh(new_formula)
    return new_formula


async def update_kpi_formula(db: AsyncSession, formula_id: UUID, formula_in: KPIFormulaUpdate):
    """ရှိပြီးသား Formula အား ပြင်ဆင်မည် (🌟 Versioning & Auto Splitting Logic ပါဝင်သည်)"""
    stmt = select(KPIFormula).where(KPIFormula.id == formula_id)
    result = await db.execute(stmt)
    formula = result.scalars().first()

    if not formula:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KPI Formula မတွေ့ရှိပါ။")

    update_data = formula_in.model_dump(exclude_unset=True)
    if "position_id" in update_data:
        position = await _resolve_formula_position(
            db, formula.department_id, update_data["position_id"]
        )
        update_data["position_name"] = position.name if position else formula.position_name

    # 🌟 Formula သည် ACTIVE, APPROVED သို့မဟုတ် ARCHIVED ဖြစ်နေလျှင် အသစ် (Version အသစ်) အဖြစ် DRAFT ပြန်လုပ်မည်
    if formula.status in [FormulaStatus.ACTIVE, FormulaStatus.APPROVED, FormulaStatus.ARCHIVED]:
        return await _create_new_formula_version(db, formula, update_data, formula_in)

    # 🌟 DRAFT သို့မဟုတ် REVIEW အဆင့်ဖြစ်နေလျှင် ထိုနေရာတွင်ပင် တိုက်ရိုက်ပြင်ဆင်မည်
    if "categories" in update_data:
        # ယခင် Rules အဟောင်းများကို ရှင်းလင်းမည်
        await db.execute(delete(KPIScoringRuleItem).where(KPIScoringRuleItem.formula_id == formula_id))
        # Rules အသစ်များကို ပြန်သွင်းမည်
        rules_to_add = _build_scoring_rule_items(formula.id, formula_in.categories)
        if rules_to_add:
            db.add_all(rules_to_add)
        # JSON format ဖြင့် Update လုပ်ရန် dictionary သို့ ပြောင်းလဲခြင်း
        update_data["categories"] = [cat.model_dump() for cat in formula_in.categories]
        await _sync_formula_categories(db, formula_in.categories)

    for field, value in update_data.items():
        setattr(formula, field, value)

    await db.commit()
    await db.refresh(formula)
    return formula


    # ==============================================================================
    # 5. Bulk KPI Calculation
    # ==============================================================================

async def bulk_calculate_kpis(
    db: AsyncSession,
    user_ids: list[UUID],
    start_date: datetime,
    end_date: datetime,
) -> dict:
    """Calculate KPI for multiple users over the given date range.

    Returns a summary dict with total, success, failed counts and error details.
    """
    success = 0
    failed = 0
    errors = []
    for uid in user_ids:
        try:
            await calculate_custom_period_kpi(db, uid, start_date, end_date)
            success += 1
        except Exception as e:
            failed += 1
            errors.append({"user_id": str(uid), "error": str(e)})
            # Log the error for debugging
            logger.exception("Bulk KPI calculation failed for user %s", uid)
    return {"total": len(user_ids), "success": success, "failed": failed, "errors": errors}

async def get_kpi_records_by_user(db: AsyncSession, user_id: UUID, period_type_str: str = "MONTHLY"):
    """ဝန်ထမ်း၏ လစဉ် KPI မှတ်တမ်းများကို Database မှ ဆွဲထုတ်ရန်"""
    try:
        period_enum = PeriodType[period_type_str.upper()]
    except KeyError:
        raise HTTPException(status_code=400, detail="မှားယွင်းသော Period Type ဖြစ်ပါသည်။")

    stmt = select(KPIRecord).where(
        KPIRecord.user_id == user_id,
        KPIRecord.period_type == period_enum
    ).order_by(KPIRecord.period_start.desc())
    
    result = await db.execute(stmt)
    return result.scalars().all()


# ==============================================================================
# 4. KPI Report Logic 
# ==============================================================================
class KpiReportService:
    @staticmethod
    def _calculate_grade(score: float) -> str:
        """Score ပေါ်မူတည်၍ Grade သတ်မှတ်ခြင်း"""
        if score >= 90: return "A (Excellent)"
        if score >= 80: return "B (Good)"
        if score >= 60: return "C (Average)"
        return "D (Needs Improvement)"

    @staticmethod
    async def generate_summary_report(
        db: AsyncSession, 
        period_type: str, 
        department_id: UUID | None = None,
        department_ids: set[str] | None = None,
    ) -> KpiReportSummaryResponse:
        
        raw_data = await kpi_repo.get_kpi_report_data(
            db, period_type, department_id, department_ids=department_ids
        )
        
        report_items = []
        total_company_score = 0.0

        for row in raw_data: # 🌟 FIXED: Reading from Dictionary instead of Object
            total_score = float(row.get("total_score", 0.0))
            total_company_score += total_score
            
            item = KpiReportItem(
                user_id=row["user_id"],
                full_name=row["full_name"],
                department_name=row["department_name"] or "N/A",
                total_score=total_score,
                # 🌟 FIXED: Using real database grade so UI Badge color works correctly
                grade=row["grade"], 
                evaluated_tasks_count=row["evaluated_tasks_count"],
                period_type=row["period_type"],
                created_at=row["latest_evaluation_date"]
            )
            report_items.append(item)

        employee_count = len(report_items)
        avg_score = (total_company_score / employee_count) if employee_count > 0 else 0.0

        return KpiReportSummaryResponse(
            period=period_type,
            total_employees_evaluated=employee_count,
            average_company_score=round(avg_score, 2),
            data=report_items
        )