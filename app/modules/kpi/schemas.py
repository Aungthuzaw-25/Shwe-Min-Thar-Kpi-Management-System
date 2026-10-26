from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.modules.kpi.models import FormulaScope, FormulaStatus, KPIStatus, PeriodType

TIMELINESS_CATEGORY_NAME = "အချိန်မှီမတင်ပြခြင်း"
TIMELINESS_CATEGORY_DESCRIPTION = "System-defined timeliness KPI category"


def _is_timeliness_category_name(name: str) -> bool:
    if not name:
        return False
    normalized = name.strip().lower()
    return (
        normalized == TIMELINESS_CATEGORY_NAME.lower()
        or "အချိန်မှီ" in normalized
        or "နောက်ကျ" in normalized
        or "late submission" in normalized
        or "late" in normalized
    )


def _normalize_timeliness_category(categories: list["KPICategoryConfig"]) -> list["KPICategoryConfig"]:
    if not categories:
        return categories

    normalized = [cat.model_copy(deep=True) for cat in categories]
    _ensure_timeliness_category(normalized)
    normalized = [
        cat
        for cat in normalized
        if not _is_timeliness_category_name(cat.name)
        or cat.name == TIMELINESS_CATEGORY_NAME
    ]
    late_category = _find_or_add_timeliness_category(normalized)
    _normalize_timeliness_categories(normalized)
    _rebalance_category_weights(normalized, late_category)
    _validate_category_weights(normalized)
    return normalized


def _new_timeliness_category() -> "KPICategoryConfig":
    return KPICategoryConfig(
        name=TIMELINESS_CATEGORY_NAME,
        description=TIMELINESS_CATEGORY_DESCRIPTION,
        weight=0.01,
        max_score=5.0,
        evaluation_type="DEDUCTION",
        deductions=[],
    )


def _ensure_timeliness_category(
    categories: list["KPICategoryConfig"],
) -> None:
    category = next(
        (cat for cat in categories if _is_timeliness_category_name(cat.name)),
        None,
    )
    if category is None:
        categories.append(_new_timeliness_category())
        return
    _normalize_single_timeliness_category(category)


def _find_or_add_timeliness_category(
    categories: list["KPICategoryConfig"],
) -> "KPICategoryConfig":
    category = next(
        (cat for cat in categories if cat.name == TIMELINESS_CATEGORY_NAME),
        None,
    )
    if category is None:
        category = _new_timeliness_category()
        categories.append(category)
    return category


def _normalize_single_timeliness_category(
    category: "KPICategoryConfig",
) -> None:
    category.name = TIMELINESS_CATEGORY_NAME
    category.description = category.description or TIMELINESS_CATEGORY_DESCRIPTION
    category.weight = max(float(category.weight), 0.01)
    category.evaluation_type = "DEDUCTION"


def _normalize_timeliness_categories(
    categories: list["KPICategoryConfig"],
) -> None:
    for category in categories:
        if category.name == TIMELINESS_CATEGORY_NAME:
            _normalize_single_timeliness_category(category)


def _rebalance_category_weights(
    categories: list["KPICategoryConfig"],
    late_category: "KPICategoryConfig",
) -> None:
    non_late = [
        category
        for category in categories
        if category.name != TIMELINESS_CATEGORY_NAME
    ]
    total_weight = sum(float(category.weight) for category in categories)
    if total_weight > 1.0:
        reduction_needed = total_weight - 1.0
        for cat in reversed(non_late):
            if cat.weight <= 0:
                continue
            amount = min(float(cat.weight), reduction_needed)
            cat.weight = max(0.0, float(cat.weight) - amount)
            reduction_needed -= amount
            if reduction_needed <= 1e-9:
                break
    elif total_weight < 1.0 and non_late:
        remainder = 1.0 - total_weight
        largest = max(non_late, key=lambda cat: float(cat.weight))
        largest.weight = float(largest.weight) + remainder

    late_category.weight = max(float(late_category.weight), 0.01)


def _validate_category_weights(categories: list["KPICategoryConfig"]) -> None:
    final_total = sum(float(category.weight) for category in categories)
    if final_total > 1.0 + 1e-9:
        raise ValueError("Timeliness category validation failed: final category weights must total 100%.")
    if final_total < 1.0 - 1e-9:
        raise ValueError("Timeliness category validation failed: final category weights must total 100%.")

# ---------------------------------------------------------
# 1. KPI Level Settings (L0, L1, L2 အဖွင့်/အပိတ်)
# ---------------------------------------------------------
class KPILevelSettingUpdate(BaseModel):
    is_enabled: bool = Field(..., description="True ဆိုလျှင် ဖွင့်မည်၊ False ဆိုလျှင် ပိတ်မည်")

class KPILevelSettingResponse(BaseModel):
    level_name: str
    is_enabled: bool

    model_config = ConfigDict(from_attributes=True)

class DepartmentLevelSettingRequest(BaseModel):
    department_id: UUID
    settings: dict[str, bool] = Field(..., description="e.g., {'L0': true, 'L1': false, 'L2': true}")

# 🌟 FIX: Added the exact strict schema for targeted department level saving
class DepartmentKPILevelUpdate(BaseModel):
    l0_enabled: bool = Field(..., description="Explicitly True or False for L0")
    l1_enabled: bool = Field(..., description="Explicitly True or False for L1")
    l2_enabled: bool = Field(..., description="Explicitly True or False for L2")

class CustomKpiCalculationRequest(BaseModel):
    start_date: datetime
    end_date: datetime


class ExplicitKpiCalculationRequest(BaseModel):
    record_id: UUID
    assignment_id: UUID | None = None
    period_id: UUID | None = None
    formula_id: UUID | None = None
    user_id: UUID | None = None


class BulkKpiCalculationRequest(BaseModel):
    user_ids: list[UUID] | None = None
    start_date: datetime
    end_date: datetime
    run_in_background: bool = False
    model_config = ConfigDict(from_attributes=True)

# ---------------------------------------------------------
# 2. KPI Formulas & Deduction Rules (Admin မှ သတ်မှတ်ချက်များ)
# ---------------------------------------------------------
class KPIDeductionRule(BaseModel):
    operator: Literal[">=", "<=", "==", ">", "<"] = ">="
    value: float = Field(..., description="ချိုးဖောက်သည့် အကြိမ်ရေ သို့မဟုတ် ရာခိုင်နှုန်း")
    deduct_score: float = Field(..., ge=0.0, le=5.0, description="Max 5 မှတ်ထဲမှ လျှော့ချမည့် အမှတ်")

class KPIDeductionItem(BaseModel):
    name: str = Field(..., description="ဥပမာ - ရုံးနောက်ကျခြင်း")
    measure_type: Literal["count", "percentage"] = Field(..., description="Count သို့မဟုတ် Percentage တစ်ခုသာဖြစ်ရမည်")
    rules: list[KPIDeductionRule] = Field(..., min_length=1)

class KPICategoryConfig(BaseModel):
    name: str = Field(..., description="Category Name (ဥပမာ - Cash Handling)")
    description: str | None = None
    weight: float = Field(..., ge=0.0, le=1.0, description="ရာခိုင်နှုန်းပြမည့် Weight (ဥပမာ ၀.၃)")
    max_score: float = Field(5.0, description="ပုံသေ ၅ မှတ်စနစ်")

    # 🌟 NEW: Target လား Deduction လား ခွဲခြားရန် Field အသစ် ဖြည့်စွက်ထားပါသည်
    evaluation_type: Literal["DEDUCTION", "TARGET"] = Field("DEDUCTION", description="Deduction သို့မဟုတ် Target")

    # Target direction: HIGHER = higher actual is better,
    # LOWER = lower actual is better. TARGET categories only — the
    # engine ignores it for DEDUCTION. None = legacy category
    # (engine falls back to the old metric_type inference).
    direction: Literal["HIGHER", "LOWER"] | None = Field(
        None, description="TARGET အတွက် HIGHER (များလေ ကောင်းလေ) သို့မဟုတ် LOWER (နည်းလေ ကောင်းလေ)"
    )

    deductions: list[KPIDeductionItem] | None = []

class KPIFormulaCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: str | None = None

    # 🌟 Targeting Fields
    region_town: str | None = Field(None, description="Region သို့မဟုတ် Town (e.g. YGN Office)")
    department_id: UUID = Field(..., description="Department မဖြစ်မနေရွေးရမည်")
    position_name: str = Field(..., description="ရာထူးကို မဖြစ်မနေ ရွေးချယ်ရမည်")
    position_id: UUID | None = Field(None, description="Authoritative position identifier")
    user_level: str = Field(..., description="L0, L1, L2")

    # 🌟 Categories and Rules
    categories: list[KPICategoryConfig] = Field(..., min_length=1)

    scope: FormulaScope = FormulaScope.DEPARTMENT
    is_active: bool = True
    effective_month: str | None = Field(None, description="e.g., 2024-11")
    expression: str | None = None  # New optional expression field
    # Periods this formula applies to (explicit links). On create:
    # links are made current. Omit on update to leave links as-is.
    period_ids: list[UUID] | None = Field(
        None, description="Periods this formula applies to (explicit links)"
    )

    @model_validator(mode='after')
    def validate_formula_rules(self):
        self.categories = _normalize_timeliness_category(self.categories)
        # New TARGET categories default to HIGHER (the common case).
        # Legacy categories are unaffected: they already carry a
        # stored direction, or no direction at all (engine fallback).
        for cat in self.categories:
            if cat.evaluation_type == "TARGET" and cat.direction is None:
                cat.direction = "HIGHER"
        total_weight = sum(cat.weight for cat in self.categories)
        if not (0.99 <= total_weight <= 1.01):
            raise ValueError(f"Category Weights စုစုပေါင်းသည် 1.0 (100%) ဖြစ်ရပါမည်။ လက်ရှိ: {total_weight}")
        late_category = next((cat for cat in self.categories if cat.name.strip() == TIMELINESS_CATEGORY_NAME), None)
        if late_category is None or float(late_category.weight) < 0.01:
            raise ValueError("အချိန်မှီမတင်ပြခြင်း category အတွက် weight သည် 1% အနည်းဆုံး ဖြစ်ရမည်။")
        return self

class KPIFormulaUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    position_id: UUID | None = None
    categories: list[KPICategoryConfig] | None = None
    is_active: bool | None = None
    effective_month: str | None = None
    # Present (even as []) = replace the current period links with
    # exactly this set; absent = leave links untouched.
    period_ids: list[UUID] | None = None

    @model_validator(mode='after')
    def validate_formula_rules(self):
        if self.categories is not None:
            self.categories = _normalize_timeliness_category(self.categories)
            total_weight = sum(cat.weight for cat in self.categories)
            if not (0.99 <= total_weight <= 1.01):
                raise ValueError(f"Category Weights စုစုပေါင်းသည် 1.0 (100%) ဖြစ်ရပါမည်။ လက်ရှိ: {total_weight}")
            late_category = next((cat for cat in self.categories if cat.name.strip() == TIMELINESS_CATEGORY_NAME), None)
            if late_category is None or float(late_category.weight) < 0.01:
                raise ValueError("အချိန်မှီမတင်ပြခြင်း category အတွက် weight သည် 1% အနည်းဆုံး ဖြစ်ရမည်။")
        return self

class KPIFormulaResponse(KPIFormulaCreate):
    id: UUID
    version: int
    status: FormulaStatus

    created_by_email: str | None = None
    updated_at: datetime | None = None
    # Lifecycle (filled by the API, not stored on the formula row):
    # current period links, closure state, and a one-word lifecycle
    # label for list display: "closed" | "linked" | "legacy".
    period_ids: list[UUID] = []
    closed_at: datetime | None = None
    is_closed: bool = False
    lifecycle_status: str = "legacy"

    model_config = ConfigDict(from_attributes=True)


class FormulaPeriodsUpdate(BaseModel):
    period_ids: list[UUID] = Field(
        ..., description="The exact set of periods this formula applies to now"
    )


class FormulaReviveRequest(BaseModel):
    # Validated in the endpoint: a revived formula MUST link at
    # least one period (clear business message, not a bare 422).
    period_ids: list[UUID] = Field(
        ..., description="Periods to link when reviving (at least one)"
    )

# ---------------------------------------------------------
# 3. KPI Records
# ---------------------------------------------------------
class CategoryExceedance(BaseModel):
    """One TARGET category's over-achievement at calculation time.

    exceeded_pct is None when not computable (no target, target <= 0,
    or no actual). actual_zero flags the LOWER + actual == 0 edge
    (a perfect zero, not an ordinary over-run)."""

    category: str
    direction: str | None = None
    target_value: float | None = None
    actual_value: float | None = None
    exceeded: bool = False
    exceeded_pct: float | None = None
    actual_zero: bool = False


class KPIRecordResponse(BaseModel):
    id: UUID
    user_id: UUID
    department_id: UUID | None
    formula_id: UUID | None

    period_type: PeriodType
    period_start: datetime
    period_end: datetime

    raw_score_total: float = Field(alias="raw_score", default=0.0)
    weighted_score: float
    percentage: float
    grade: KPIStatus

    exceeded_categories: list[CategoryExceedance] | None = None

    is_frozen: bool = False
    is_overridden: bool = False
    override_reason: str | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

# ---------------------------------------------------------
# 4. KPI History (Audit Trail)
# ---------------------------------------------------------
class KPIHistoryResponse(BaseModel):
    id: UUID
    kpi_record_id: UUID
    changed_by_id: UUID

    old_percentage: float
    new_percentage: float
    old_weighted_score: float | None = None
    new_weighted_score: float | None = None
    old_status: KPIStatus
    new_status: KPIStatus
    reason: str

    model_config = ConfigDict(from_attributes=True)

# ==============================================================================
# 5. Admin Intervention (Override, Deduct, Freeze) Schemas
# ==============================================================================
class KPIOverrideRequest(BaseModel):
    new_score: float = Field(..., ge=0.0, le=5.0, description="အစားထိုးမည့် ရမှတ်သစ် (၀ မှ ၅ ကြား)")
    reason: str = Field(..., description="ပြင်ဆင်ရသည့် အကြောင်းရင်း (Audit အတွက် မဖြစ်မနေ ထည့်ရန်)")

class KPIDeductionRequest(BaseModel):
    deduct_points: float = Field(..., gt=0.0, description="လျှော့မည့် ရမှတ် (ဥပမာ ၀.၅)")
    reason: str = Field(..., description="အမှတ်လျှော့ရသည့် အကြောင်းရင်း")

class KPIFreezeRequest(BaseModel):
    is_frozen: bool = Field(..., description="True (အသေထားမည်) သို့မဟုတ် False (ပြန်ဖွင့်မည်)")

# ==============================================================================
# 6. Dashboard: Charts & Ranking Schemas
# ==============================================================================
class KPIRankingResponse(BaseModel):
    user_id: UUID
    user_name: str
    department_name: str
    total_score: float
    percentage: float
    grade: str
    rank: int
    # Departed-employee label: render "Resigned · <resigned_at>".
    resigned_at: date | None = None
    is_departed: bool = False

    model_config = ConfigDict(from_attributes=True)

class KPIChartPoint(BaseModel):
    date_label: str = Field(..., description="ပြသမည့် နေ့စွဲ သို့မဟုတ် လ (ဥပမာ - 2023-10-01, Oct-2023)")
    score: float
    percentage: float

class KPIChartResponse(BaseModel):
    user_id: UUID
    period_type: PeriodType
    data_points: list[KPIChartPoint]

# ==============================================================================
# 7. KPI Report Schemas
# ==============================================================================
class KpiReportItem(BaseModel):
    user_id: UUID
    full_name: str
    avatar_url: str | None = None
    initials: str = "?"
    avatar_color_index: int = 0
    department_name: str | None = None
    total_score: float
    grade: str
    evaluated_tasks_count: int
    period_type: str
    created_at: datetime | None = None
    # Departed-employee label: render "Resigned · <resigned_at>".
    resigned_at: date | None = None
    is_departed: bool = False

class KpiReportSummaryResponse(BaseModel):
    success: bool = True
    message: str = "KPI Report generated successfully"
    period: str
    total_employees_evaluated: int
    average_company_score: float
    data: list[KpiReportItem]