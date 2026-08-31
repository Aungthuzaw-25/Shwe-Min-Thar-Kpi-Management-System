import logging
from typing import List, Dict, Optional, Tuple
from datetime import datetime
import calendar
import operator
import re

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_, and_, func
from sqlalchemy.orm import selectinload 
from uuid import UUID

from app.modules.kpi.models import KPIStatus, KPIRecord, PeriodType, KPIHistory
from app.modules.kpi.models import KPILevelSetting, KPIFormula, FormulaScope, FormulaStatus, KPIScoringRuleItem, KPIPeriod, KPIAssignment
from app.modules.users.models import User, Position 
from app.modules.departments.models import UserDepartment 

from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_review import TaskReview

from app.modules.targets.models import Target 

# Configure Module Logger for Observability
logger = logging.getLogger(__name__)

# Python Operators Mapping for dynamic mathematical parsing
OPERATORS = {
    ">=": operator.ge, "<=": operator.le, 
    "==": operator.eq, ">": operator.gt, "<": operator.lt
}

# 🌟 ENTERPRISE FIX: Extract hardcoded localization strings to maintainable constants
LATE_SUBMISSION_KEYWORDS = ["late", "အချိန်", "နောက်ကျ"]
DEFAULT_LATE_CAT_NAME = "အချိန်မှီမတင်ပြခြင်း"


# ==============================================================================
# 1. KPI Aggregation Engine 
# ==============================================================================
class KPICalculationEngine:
    
    @staticmethod
    def standardize_key(key_str: str) -> str:
        """
        🌟 SENIOR FIX: String Matching Bug
        Space အပိုများကို ဖယ်ရှား၍ စာလုံးအသေးသို့ ပြောင်းလဲပေးမည်။ 
        Excel Column များနှင့် Rule Name များ လွဲချော်မှုကို အပြည့်အဝ ကာကွယ်ပေးသည်။
        """
        if not key_str: return ""
        return re.sub(r'\s+', ' ', str(key_str).strip().lower())

    @staticmethod
    def get_grade(percentage: float) -> KPIStatus:
        """Determines the KPI Grade based on percentage performance."""
        if percentage >= 90.0: return KPIStatus.EXCELLENT
        elif percentage >= 80.0: return KPIStatus.VERY_GOOD
        elif percentage >= 60.0: return KPIStatus.GOOD
        elif percentage >= 40.0: return KPIStatus.FAIR
        else: return KPIStatus.POOR

    @staticmethod
    def evaluate_db_rules(cat_name: str, rules: List[KPIScoringRuleItem], evaluated_data: dict, max_score: float = 5.0) -> float:
        """
        Checks BOTH the Category Name (from Excel) AND the Rule Name (from System Workflow)
        to ensure no deductions are missed. Uses highest applicable deduction per rule.
        """
        current_score = float(max_score)
        std_cat_name = KPICalculationEngine.standardize_key(cat_name)
        
        # Extract base value from Excel data using Category Name
        base_actual_value = 0.0
        if std_cat_name in evaluated_data:
            try:
                base_actual_value = float(evaluated_data[std_cat_name])
            except (ValueError, TypeError):
                logger.warning(f"Invalid numeric value in evaluated_data for key: {std_cat_name}")
                
        grouped_rules = {}
        for r in rules:
            std_rule_name = KPICalculationEngine.standardize_key(r.rule_name)
            grouped_rules.setdefault(std_rule_name, []).append(r)
            
        total_deduction_for_category = 0.0
            
        for std_rule_name, rule_items in grouped_rules.items():
            # Combine System value (by rule_name) and Excel value (by cat_name)
            actual_value = base_actual_value
            if std_rule_name and std_rule_name in evaluated_data:
                try:
                    actual_value = max(actual_value, float(evaluated_data[std_rule_name]))
                except (ValueError, TypeError):
                    logger.warning(f"Invalid numeric value in evaluated_data for rule: {std_rule_name}")
                
            highest_deduction = 0.0
            
            for r in rule_items:
                op_func = OPERATORS.get(r.operator)
                if not op_func: 
                    logger.error(f"Unsupported operator '{r.operator}' in rule '{r.rule_name}'")
                    continue
                
                try:
                    target_val = float(r.target_value)
                    # Apply the specific HR Step-Tier logic strictly
                    if op_func(actual_value, target_val):
                        deduct_amt = float(r.deduct_score)
                        if deduct_amt > highest_deduction:
                            highest_deduction = deduct_amt
                except (ValueError, TypeError) as e:
                    logger.error(f"Failed to parse rule values for '{r.rule_name}': {e}")
                        
            total_deduction_for_category += highest_deduction
            
        return max(0.0, current_score - total_deduction_for_category)

    @staticmethod
    async def get_applicable_formula_and_period(db: AsyncSession, user: User, target_date: datetime) -> Tuple[Optional[KPIFormula], Optional[KPIPeriod]]:
        """
        🌟 Fully Relational Connection:
        Period ကို ရှာမည် -> ထို Period တွင် Assign လုပ်ထားသော Assignment ကို ရှာမည် -> Formula ကို ဆွဲထုတ်မည်။
        """
        stmt_period = select(KPIPeriod).where(
            KPIPeriod.start_date <= target_date,
            KPIPeriod.end_date >= target_date,
            KPIPeriod.is_deleted == False
        )
        period = (await db.execute(stmt_period)).scalars().first()
        
        formula = None
        
        if period:
            stmt_assign = select(KPIAssignment).where(
                KPIAssignment.user_id == user.id,
                KPIAssignment.kpi_period_id == period.id,
                KPIAssignment.is_deleted == False
            )
            assignment = (await db.execute(stmt_assign)).scalars().first()
            
            if assignment:
                stmt_form = select(KPIFormula).options(selectinload(KPIFormula.scoring_rules)).where(
                    KPIFormula.id == assignment.kpi_formula_id,
                    KPIFormula.status == FormulaStatus.ACTIVE
                )
                formula = (await db.execute(stmt_form)).scalars().first()
                
        if not formula:
            dept_id = user.user_departments[0].department_id if hasattr(user, 'user_departments') and user.user_departments else None
            if not dept_id and hasattr(user, 'position') and user.position:
                dept_id = user.position.department_id
                
            pos_name = user.position.name if hasattr(user, 'position') and user.position else "Unknown"
            region_town = getattr(user, 'region_town', None)
            
            if dept_id:
                stmt_form = select(KPIFormula).options(selectinload(KPIFormula.scoring_rules)).where(
                    KPIFormula.department_id == dept_id,
                    KPIFormula.position_name == pos_name,
                    KPIFormula.status == FormulaStatus.ACTIVE
                )
                if region_town:
                    stmt_form = stmt_form.where(KPIFormula.region_town == region_town)
                    
                formula = (await db.execute(stmt_form)).scalars().first()
                
        return formula, period

    @staticmethod
    async def execute_kpi_calculation(db: AsyncSession, user: User, period_start: datetime, period_end: datetime, period_type: PeriodType, task_data: dict = None):
        """
        Core Engine: Aggregates tasks, parses text-based deductions, calculates target completion, 
        and updates or creates the final KPI Record securely with Transaction rollback protection.
        """
        formula, period = await KPICalculationEngine.get_applicable_formula_and_period(db, user, period_start)
        
        if not formula: 
            logger.info(f"No active formula found for user {user.id}")
            return None
        
        dept_id = formula.department_id

        deduction_counts = {}
        deduction_values = {}

        # 🌟 SENIOR FIX 4: The Time-Boundary Leak 
        # Task ပြီးစီးရမည့်ရက် (due_date) ကို အဓိကထား၍ လပိုင်းခြားမည်။ 
        # due_date မပါရှိမှသာ created_at ကို အသုံးပြုမည်။
        period_start_naive = period_start.replace(tzinfo=None)
        period_end_naive = period_end.replace(tzinfo=None)

        try:
            # ======================================================================
            # 🌟 (၁) အလုပ်မှားယွင်းမှုအတွက် (Reject) Penalty စစ်ဆေးခြင်း
            # ======================================================================
            stmt_l2 = select(TaskReview.comments).join(Task, TaskReview.task_id == Task.id).where(
                Task.owner_id == user.id,
                TaskReview.action.in_(["REJECTED", "REJECTED_OVERRIDE"]),
                TaskReview.reviewer_level.in_(["L1", "Level 1"]), 
                or_(
                    Task.due_date.between(period_start_naive, period_end_naive),
                    and_(Task.due_date.is_(None), Task.created_at.between(period_start_naive, period_end_naive))
                )
            )
            l2_reviews = (await db.execute(stmt_l2)).scalars().all()

            # 🌟 FIXED: Added .scalar_subquery() for SQLAlchemy 2.0 compatibility
            stmt_l1 = select(TaskReview.comments).join(Task, TaskReview.task_id == Task.id).where(
                TaskReview.task_id.in_(
                    select(TaskReview.task_id).where(
                        TaskReview.reviewer_id == user.id,
                        TaskReview.action == "APPROVED",
                        TaskReview.reviewer_level.in_(["L1", "Level 1"])
                    ).scalar_subquery()
                ),
                TaskReview.reviewer_level.in_(["L0", "Level 0"]),
                TaskReview.action.in_(["REJECTED", "REJECTED_OVERRIDE"]),
                or_(
                    Task.due_date.between(period_start_naive, period_end_naive),
                    and_(Task.due_date.is_(None), Task.created_at.between(period_start_naive, period_end_naive))
                )
            ) 

            l1_reviews = (await db.execute(stmt_l1)).scalars().all()

            for comment in l2_reviews + l1_reviews:
                if not comment: continue
                
                rule_match = re.search(r"\[KPI Deduction:\s*(.*?)\]", comment)
                if not rule_match: continue
                    
                rule_name = rule_match.group(1).strip()
                
                val_match = re.search(r"\[Value:\s*([\d\.]+)\]", comment)
                
                if val_match:
                    try:
                        val = float(val_match.group(1))
                        if rule_name not in deduction_values:
                            deduction_values[rule_name] = []
                        deduction_values[rule_name].append(val)
                    except ValueError:
                        logger.warning(f"Invalid rule value parsing in comment: {comment}")
                else:
                    deduction_counts[rule_name] = deduction_counts.get(rule_name, 0) + 1

            # ======================================================================
            # 🌟 (၂) အချိန်နောက်ကျမှုအတွက် (Late Submission) Penalty စစ်ဆေးခြင်း
            # ======================================================================
            stmt_late = select(func.count(Task.id)).where(
                Task.owner_id == user.id,
                Task.is_late == True,  # Assigned Task တွေမှာသာ True ဖြစ်နိုင်သည်
                or_(
                    Task.due_date.between(period_start_naive, period_end_naive),
                    and_(Task.due_date.is_(None), Task.created_at.between(period_start_naive, period_end_naive))
                )
            )
            late_tasks_count = (await db.execute(stmt_late)).scalar() or 0

            if late_tasks_count > 0:
                # Formula ထဲမှာ "အချိန်မှီမတင်ပြခြင်း" နဲ့သက်ဆိုင်တဲ့ Category ကို ရှာမည်
                late_cat_name = DEFAULT_LATE_CAT_NAME 
                for cat in formula.categories:
                    c_name = cat.get("name", "")
                    # "late", "အချိန်", "နောက်ကျ" စသည့် keyword များကို ရှာဖွေမည်
                    if any(keyword in c_name.lower() for keyword in LATE_SUBMISSION_KEYWORDS):
                        late_cat_name = c_name
                        break
                
                # တွေ့ရှိသော Late Tasks အရေအတွက်ကို Deduction Counts ထဲသို့ ထပ်ပေါင်းထည့်မည် (Dual Penalty အလုပ်လုပ်ရန်)
                deduction_counts[late_cat_name] = deduction_counts.get(late_cat_name, 0) + late_tasks_count

            excel_manual_data = task_data if task_data is not None else {}
            final_merged_data = {}
            
            # ======================================================================
            # 🌟 (၃) Merge Collision & min() Aggregation Fallacy ကို ဖြေရှင်းခြင်း
            # ======================================================================
            
            # (၁) List ဖြင့် လက်ခံထားသော Value များကို ပေါင်းစပ်မည် (e.g. ပျောက်ဆုံးငွေ၊ ရာခိုင်နှုန်း)
            for k, v_list in deduction_values.items():
                std_k = KPICalculationEngine.standardize_key(k)
                # အကယ်၍ တန်ဖိုးအားလုံး 100 အောက်ဖြစ်ပါက Percentage ဟု ယူဆ၍ Worst Case (min) ကို ယူမည်။
                # 100 ထက်ကျော်လွန်ပါက Amount ဟု ယူဆ၍ စုစုပေါင်း အမှားပမာဏ (sum) ကို ပေါင်းမည်။
                if all(v <= 100 for v in v_list):
                    aggregated_val = min(v_list)
                else:
                    aggregated_val = sum(v_list)
                final_merged_data[std_k] = final_merged_data.get(std_k, 0.0) + aggregated_val

            # (၂) System ၏ အကြိမ်ရေ (Count) များကို ပေါင်းစပ်မည် (Reject + Late Counts ပါဝင်သည်)
            for k, v in deduction_counts.items():
                std_k = KPICalculationEngine.standardize_key(k)
                final_merged_data[std_k] = final_merged_data.get(std_k, 0.0) + float(v)

            # (၃) HR မှ Upload တင်သော Excel Data ကို Additive Merge လုပ်မည်
            for k, v in excel_manual_data.items():
                std_k = KPICalculationEngine.standardize_key(k)
                try:
                    final_merged_data[std_k] = final_merged_data.get(std_k, 0.0) + float(v)
                except ValueError:
                    logger.warning(f"Skipping non-numeric manual data for key {k}")
            
            # ======================================================================
            # 🌟 HYBRID SCORING ENGINE (TARGET-BASED vs DEDUCTION-BASED)
            # ======================================================================
            total_weighted_score = 0.0
            target_month_str = period_start.strftime("%Y-%m")

            for category in formula.categories:
                cat_name = category.get("name", "")
                std_cat_name = KPICalculationEngine.standardize_key(cat_name)
                
                raw_weight = float(category.get("weight", 0.0))
                cat_weight = raw_weight if raw_weight <= 1.0 else raw_weight / 100.0 
                max_score = float(category.get("max_score", 5.0))
                
                eval_type = category.get("evaluation_type", "DEDUCTION")
                cat_actual_score = max_score
                
                if eval_type == "TARGET":
                    # 🎯 TARGET-BASED LOGIC
                    stmt_target = select(Target).where(
                        Target.target_month == target_month_str,
                        or_(Target.department_id == dept_id, Target.department_id.is_(None))
                    )
                    targets = (await db.execute(stmt_target)).scalars().all()
                    
                    assigned_target = None
                    for t in targets:
                        if KPICalculationEngine.standardize_key(t.name) == std_cat_name:
                            if t.department_id == dept_id:
                                assigned_target = t
                                break
                            elif not assigned_target:
                                assigned_target = t 

                    if assigned_target and assigned_target.target_value > 0:
                        target_val = float(assigned_target.target_value)
                        actual_val = float(final_merged_data.get(std_cat_name, 0.0))
                        
                        # 🌟 FIXED: Safe evaluation of metric_type to prevent AttributeError
                        metric_type_attr = getattr(assigned_target, 'metric_type', '')
                        metric_type_val = getattr(metric_type_attr, 'value', str(metric_type_attr)).upper()
                        
                        is_lower_better = "COUNT" in metric_type_val 
                        
                        if target_val == 0.0:
                            achievement_ratio = 1.0
                        else:
                            if is_lower_better:
                                # ဥပမာ - စက်ပျက်ချိန်: Target 5 hrs, Actual 10 hrs => 5 / 10 = 0.5 (50% ပဲရမည်)
                                achievement_ratio = target_val / actual_val if actual_val > 0 else 1.0
                            else:
                                # ဥပမာ - Sales: Target 1M, Actual 800k => 800k / 1M = 0.8 (80% ရမည်)
                                achievement_ratio = actual_val / target_val
                                
                        achievement_ratio = min(1.0, max(0.0, achievement_ratio)) # Clamped Score
                        cat_actual_score = max_score * achievement_ratio
                    else:
                        cat_actual_score = 0.0

                else:
                    # 📉 DEDUCTION-BASED LOGIC
                    cat_rules = [r for r in formula.scoring_rules if r.category_name == cat_name]
                    if cat_rules:
                        cat_actual_score = KPICalculationEngine.evaluate_db_rules(cat_name, cat_rules, final_merged_data, max_score)
                
                total_weighted_score += (cat_actual_score * cat_weight)

            # Final Percentage & Grade Computation
            percentage = min(100.0, max(0.0, (total_weighted_score / 5.0) * 100.0))
            grade = KPICalculationEngine.get_grade(percentage)

            period_id = period.id if period else None

            stmt = select(KPIRecord).where(
                KPIRecord.user_id == user.id,
                KPIRecord.period_type == period_type,
                KPIRecord.period_start == period_start
            )
            record = (await db.execute(stmt)).scalars().first()
            
            if record:
                if record.is_frozen or record.is_overridden: return
                record.weighted_score = total_weighted_score
                record.percentage = percentage
                record.status_bucket = grade
                record.kpi_period_id = period_id # Update relation
            else:
                new_record = KPIRecord(
                    user_id=user.id, 
                    department_id=dept_id, 
                    formula_id=formula.id,
                    period_type=period_type, 
                    period_start=period_start, 
                    period_end=period_end,
                    kpi_period_id=period_id, 
                    weighted_score=total_weighted_score, 
                    percentage=percentage, 
                    status_bucket=grade
                )
                db.add(new_record)
                
            await db.commit()

        except Exception as e:
            await db.rollback()
            logger.error(f"Critical error calculating KPI for user {user.id}: {str(e)}", exc_info=True)
            raise

    @staticmethod
    async def calculate_monthly_kpi_for_user(db: AsyncSession, user: User, target_date: datetime, task_data: dict = None):
        """Helper to invoke monthly calculation logic correctly parsing dates"""
        period_start = target_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_day = calendar.monthrange(target_date.year, target_date.month)[1]
        period_end = target_date.replace(day=last_day, hour=23, minute=59, second=59, microsecond=999999)

        await KPICalculationEngine.execute_kpi_calculation(db, user, period_start, period_end, PeriodType.MONTHLY, task_data)


# ==============================================================================
# 2. Support Helper Functions
# ==============================================================================
async def is_level_kpi_enabled(db: AsyncSession, level_name: str, department_id: Optional[UUID] = None) -> bool:
    """Checks if KPI evaluation is administratively unblocked for a user level."""
    stmt = select(KPILevelSetting).where(KPILevelSetting.level_name == level_name)
    
    if department_id:
        stmt = stmt.where(KPILevelSetting.department_id == department_id)
        
    setting = (await db.execute(stmt)).scalars().first()
    return setting.is_enabled if setting else False


# ==============================================================================
# 3. Master Routing Operations
# ==============================================================================
async def trigger_kpi_recalculation(db: AsyncSession, user_level: str, user_id: UUID, target_date: datetime, department_id: Optional[UUID] = None):
    """Entry Point for on-the-fly recalculations whenever a task is processed."""
    
    stmt = select(User).options(
        selectinload(User.user_departments),
        selectinload(User.position)
    ).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    
    if not user: return

    if not department_id:
        if user.user_departments:
            department_id = user.user_departments[0].department_id
        elif user.position:
            department_id = user.position.department_id

    if not department_id: return
    if not await is_level_kpi_enabled(db, user_level, department_id): return

    await KPICalculationEngine.calculate_monthly_kpi_for_user(db, user, target_date)

async def calculate_custom_period_kpi(db: AsyncSession, user_id: UUID, start_date: datetime, end_date: datetime, task_data: dict = None):
    """Generates ad-hoc KPI records for custom manual evaluation scopes."""
    stmt = select(User).options(
        selectinload(User.user_departments),
        selectinload(User.position)
    ).where(User.id == user_id)
    user = (await db.execute(stmt)).scalars().first()
    
    if not user: return
    
    await KPICalculationEngine.execute_kpi_calculation(db, user, start_date, end_date, PeriodType.MONTHLY, task_data)