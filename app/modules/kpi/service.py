from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete # 🌟 ADDED: For updating rules cleanly
from fastapi import HTTPException, status
from uuid import UUID
from typing import Optional

# 🌟 IMPORT: Added KPIScoringRuleItem
from app.modules.kpi.models import KPILevelSetting, KPIFormula, KPIRecord, PeriodType, FormulaStatus, KPIScoringRuleItem
from app.modules.kpi.schemas import (
    KPIFormulaCreate, 
    KPIFormulaUpdate, 
    KPILevelSettingUpdate,
    KpiReportSummaryResponse,
    KpiReportItem
)

from app.modules.kpi import repo as kpi_repo 

# 🌟 FIX: Import KPICalculationEngine to use standardize_key
from app.modules.kpi.engine import KPICalculationEngine

# ==============================================================================
# 1. KPI Level Settings Logic (L0, L1, L2 အဖွင့်အပိတ် ပြုလုပ်ခြင်း)
# ==============================================================================
async def get_kpi_level_settings(db: AsyncSession, department_id: Optional[UUID] = None):
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

    new_formula = KPIFormula(
        name=formula_in.name,
        description=formula_in.description,
        region_town=formula_in.region_town,
        department_id=formula_in.department_id,
        position_name=formula_in.position_name,
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

async def update_kpi_formula(db: AsyncSession, formula_id: UUID, formula_in: KPIFormulaUpdate):
    """ရှိပြီးသား Formula အား ပြင်ဆင်မည် (🌟 Versioning & Auto Splitting Logic ပါဝင်သည်)"""
    stmt = select(KPIFormula).where(KPIFormula.id == formula_id)
    result = await db.execute(stmt)
    formula = result.scalars().first()

    if not formula:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="KPI Formula မတွေ့ရှိပါ။")

    update_data = formula_in.model_dump(exclude_unset=True)
    
    # 🌟 Formula သည် ACTIVE, APPROVED သို့မဟုတ် ARCHIVED ဖြစ်နေလျှင် အသစ် (Version အသစ်) အဖြစ် DRAFT ပြန်လုပ်မည်
    if formula.status in [FormulaStatus.ACTIVE, FormulaStatus.APPROVED, FormulaStatus.ARCHIVED]:
        
        new_categories = formula.categories
        if "categories" in update_data:
             new_categories = [cat for cat in update_data["categories"]]

        new_formula = KPIFormula(
            name=update_data.get("name", formula.name),
            description=update_data.get("description", formula.description),
            region_town=formula.region_town, 
            department_id=formula.department_id, 
            position_name=formula.position_name, 
            user_level=formula.user_level, 
            categories=new_categories,
            scope=formula.scope,
            is_active=update_data.get("is_active", True),
            version=formula.version + 1,            # 🌟 Version 1 ဆင့်တက်မည်
            status=FormulaStatus.DRAFT,             # 🌟 DRAFT သို့ ပြန်ရောက်မည်
            parent_formula_id=formula.id            # 🌟 မူရင်း Formula ကို မှတ်သားထားမည်
        )
        db.add(new_formula)
        await db.flush() # Get new ID
        
        # သွင်းလိုက်သော Version အသစ်အတွက် Rules အသစ်များကိုပါ kpi_scoring_rule_items ထဲသို့ ထည့်မည်
        if formula_in.categories:
            rules_to_add = []
            for cat in formula_in.categories:
                for ded in cat.deductions:
                    for rule in ded.rules:
                        rules_to_add.append(KPIScoringRuleItem(
                            formula_id=new_formula.id, 
                            # 🌟 FIX: Standardize names before saving
                            category_name=KPICalculationEngine.standardize_key(cat.name), 
                            rule_name=KPICalculationEngine.standardize_key(ded.name), 
                            measure_type=ded.measure_type, operator=rule.operator, target_value=float(rule.value), deduct_score=float(rule.deduct_score)
                        ))
            if rules_to_add:
                db.add_all(rules_to_add)

        await db.commit()
        await db.refresh(new_formula)
        return new_formula

    # 🌟 DRAFT သို့မဟုတ် REVIEW အဆင့်ဖြစ်နေလျှင် ထိုနေရာတွင်ပင် တိုက်ရိုက်ပြင်ဆင်မည်
    if "categories" in update_data:
        # ယခင် Rules အဟောင်းများကို ရှင်းလင်းမည်
        await db.execute(delete(KPIScoringRuleItem).where(KPIScoringRuleItem.formula_id == formula_id))
        
        # Rules အသစ်များကို ပြန်သွင်းမည်
        rules_to_add = []
        for cat in formula_in.categories:
            for ded in cat.deductions:
                for rule in ded.rules:
                    rules_to_add.append(KPIScoringRuleItem(
                        formula_id=formula.id, 
                        # 🌟 FIX: Standardize names before saving
                        category_name=KPICalculationEngine.standardize_key(cat.name), 
                        rule_name=KPICalculationEngine.standardize_key(ded.name), 
                        measure_type=ded.measure_type, operator=rule.operator, target_value=float(rule.value), deduct_score=float(rule.deduct_score)
                    ))
        if rules_to_add:
            db.add_all(rules_to_add)
            
        # JSON format ဖြင့် Update လုပ်ရန် dictionary သို့ ပြောင်းလဲခြင်း
        update_data["categories"] = [cat.model_dump() for cat in formula_in.categories]

    for field, value in update_data.items():
        setattr(formula, field, value)

    await db.commit()
    await db.refresh(formula)
    return formula


# ==============================================================================
# 3. KPI Records Logic 
# ==============================================================================
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
        department_id: Optional[UUID] = None
    ) -> KpiReportSummaryResponse:
        
        raw_data = await kpi_repo.get_kpi_report_data(db, period_type, department_id)
        
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