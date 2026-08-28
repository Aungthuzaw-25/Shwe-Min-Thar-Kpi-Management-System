from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Optional, Dict, Any, List, Literal
from uuid import UUID
from datetime import datetime
from app.modules.kpi.models import FormulaScope, PeriodType, KPIStatus, FormulaStatus

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
    settings: Dict[str, bool] = Field(..., description="e.g., {'L0': true, 'L1': false, 'L2': true}")

# 🌟 FIX: Added the exact strict schema for targeted department level saving
class DepartmentKPILevelUpdate(BaseModel):
    l0_enabled: bool = Field(..., description="Explicitly True or False for L0")
    l1_enabled: bool = Field(..., description="Explicitly True or False for L1")
    l2_enabled: bool = Field(..., description="Explicitly True or False for L2")

class CustomKpiCalculationRequest(BaseModel):
    start_date: datetime
    end_date: datetime

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
    rules: List[KPIDeductionRule] = Field(..., min_length=1)

class KPICategoryConfig(BaseModel):
    name: str = Field(..., description="Category Name (ဥပမာ - Cash Handling)")
    description: Optional[str] = None
    weight: float = Field(..., ge=0.0, le=1.0, description="ရာခိုင်နှုန်းပြမည့် Weight (ဥပမာ ၀.၃)")
    max_score: float = Field(5.0, description="ပုံသေ ၅ မှတ်စနစ်")
    
    # 🌟 NEW: Target လား Deduction လား ခွဲခြားရန် Field အသစ် ဖြည့်စွက်ထားပါသည်
    evaluation_type: Literal["DEDUCTION", "TARGET"] = Field("DEDUCTION", description="Deduction သို့မဟုတ် Target")
    
    deductions: Optional[List[KPIDeductionItem]] = []

class KPIFormulaCreate(BaseModel):
    name: str = Field(..., max_length=100)
    description: Optional[str] = None
    
    # 🌟 Targeting Fields
    region_town: Optional[str] = Field(None, description="Region သို့မဟုတ် Town (e.g. YGN Office)")
    department_id: UUID = Field(..., description="Department မဖြစ်မနေရွေးရမည်")
    position_name: str = Field(..., description="ရာထူးကို မဖြစ်မနေ ရွေးချယ်ရမည်")
    user_level: str = Field(..., description="L0, L1, L2")
    
    # 🌟 Categories and Rules
    categories: List[KPICategoryConfig] = Field(..., min_length=1)
    
    scope: FormulaScope = FormulaScope.DEPARTMENT
    is_active: bool = True
    effective_month: Optional[str] = Field(None, description="e.g., 2024-11")

    @model_validator(mode='after')
    def validate_formula_rules(self):
        total_weight = sum(cat.weight for cat in self.categories)
        if not (0.99 <= total_weight <= 1.01):
            raise ValueError(f"Category Weights စုစုပေါင်းသည် 1.0 (100%) ဖြစ်ရပါမည်။ လက်ရှိ: {total_weight}")
        return self

class KPIFormulaUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    categories: Optional[List[KPICategoryConfig]] = None
    is_active: Optional[bool] = None
    effective_month: Optional[str] = None

class KPIFormulaResponse(KPIFormulaCreate):
    id: UUID
    version: int
    status: FormulaStatus
    
    created_by_email: Optional[str] = None
    updated_at: Optional[datetime] = None
    
    model_config = ConfigDict(from_attributes=True)

# ---------------------------------------------------------
# 3. KPI Records
# ---------------------------------------------------------
class KPIRecordResponse(BaseModel):
    id: UUID
    user_id: UUID
    department_id: Optional[UUID]
    formula_id: Optional[UUID]
    
    period_type: PeriodType
    period_start: datetime
    period_end: datetime
    
    raw_score_total: float = Field(alias="raw_score", default=0.0) 
    weighted_score: float
    percentage: float
    grade: KPIStatus       
    
    is_frozen: bool = False
    is_overridden: bool = False
    override_reason: Optional[str] = None 
    
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

    model_config = ConfigDict(from_attributes=True)

class KPIChartPoint(BaseModel):
    date_label: str = Field(..., description="ပြသမည့် နေ့စွဲ သို့မဟုတ် လ (ဥပမာ - 2023-10-01, Oct-2023)")
    score: float
    percentage: float

class KPIChartResponse(BaseModel):
    user_id: UUID
    period_type: PeriodType
    data_points: List[KPIChartPoint]
    
# ==============================================================================
# 7. KPI Report Schemas 
# ==============================================================================
class KpiReportItem(BaseModel):
    user_id: UUID
    full_name: str
    department_name: Optional[str] = None
    total_score: float
    grade: str
    evaluated_tasks_count: int
    period_type: str
    created_at: Optional[datetime] = None

class KpiReportSummaryResponse(BaseModel):
    success: bool = True
    message: str = "KPI Report generated successfully"
    period: str
    total_employees_evaluated: int
    average_company_score: float
    data: List[KpiReportItem]