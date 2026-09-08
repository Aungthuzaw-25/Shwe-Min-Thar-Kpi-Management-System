"""
Enterprise KPI Domain Models (Single Source of Truth)
Enhanced with High-Performance Indexing & Referential Integrity
"""
import enum
from sqlalchemy import (
    Column, String, Float, Boolean, ForeignKey, DateTime, 
    Enum as SQLEnum, Text, JSON, Integer, Index, UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from app.database.base import BaseModel
from sqlalchemy.sql import func

DEPARTMENTS_ID = "departments.id"
POSITIONS_ID = "positions.id"
KPI_FORMULAS_ID = "kpi_formulas.id"
USERS_ID = "users.id"
SET_NULL = "SET NULL"
ALL_DELETE_ORPHAN = "all, delete-orphan"

# =============================================================================
# 🌟 1. ENUMS (String-backed Enums for Zero-Serialization Bugs)
# =============================================================================
class FormulaScope(str, enum.Enum):
    """Formula အကျုံးဝင်မည့် နယ်ပယ် သတ်မှတ်ချက်"""
    COMPANY = "COMPANY"           # ကုမ္ပဏီတစ်ခုလုံးအတွက် အများသုံး
    DEPARTMENT = "DEPARTMENT"     # ဌာနအလိုက် သီးသန့်
    ROLE_LEVEL = "ROLE_LEVEL"     # ရာထူး (L0, L1, L2) အလိုက် သီးသန့်

class FormulaStatus(str, enum.Enum):
    """Formula Lifecycle Workflow Statuses"""
    DRAFT = "DRAFT"
    REVIEW = "REVIEW"
    APPROVED = "APPROVED"
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"

class PeriodType(str, enum.Enum):
    """KPI Evaluation Frequency"""
    DAILY = "DAILY"
    WEEKLY = "WEEKLY"
    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"

class KPIStatus(str, enum.Enum):
    """Enterprise ၅-ဆင့် သတ်မှတ်ချက် (EXCELLENT မှ POOR အထိ)"""
    EXCELLENT = "EXCELLENT"  # 90-100%
    VERY_GOOD = "VERY_GOOD"  # 80-89%
    GOOD = "GOOD"            # 60-79%
    FAIR = "FAIR"            # 40-59%
    POOR = "POOR"            # 0-39%

class KPIPeriodStatus(str, enum.Enum):
    """Lifecycle statuses for KPI Evaluation Cycles"""
    DRAFT = "DRAFT"
    OPEN = "OPEN"
    IN_PROGRESS = "IN_PROGRESS"
    REVIEW = "REVIEW"
    FINALIZED = "FINALIZED"
    LOCKED = "LOCKED"
    CANCELLED = "CANCELLED"

class ActionPlanStatus(str, enum.Enum):
    """Performance Improvement Plan (PIP) Lifecycle"""
    DRAFT = "DRAFT"
    SUBMITTED = "SUBMITTED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"

# =============================================================================
# 🌟 2. DATA MODELS
# =============================================================================

class KPILevelSetting(BaseModel):
    """L0, L1, L2 များအတွက် KPI တွက်/မတွက် အဖွင့်အပိတ်လုပ်ရန်"""
    __tablename__ = "kpi_level_settings"
    
    department_id = Column(UUID(as_uuid=True), ForeignKey(DEPARTMENTS_ID, ondelete="CASCADE"), nullable=True, index=True)
    level_name = Column(String(10), nullable=False)  # 'L0', 'L1', 'L2'
    is_enabled = Column(Boolean, default=False, nullable=False)
    
    # Relationships
    department = relationship("Department")

    __table_args__ = (
        Index('idx_level_setting_lookup', 'department_id', 'level_name'),
    )


class KPIFormula(BaseModel):
    """Task 5.1: Configurable Enterprise KPI Formulas"""
    __tablename__ = "kpi_formulas"
    
    name = Column(String(100), nullable=False)
    description = Column(Text, nullable=True)
    expression = Column(Text, nullable=True) 
    
    # Targeting & Organizational Mapping
    region_town = Column(String(100), nullable=True, index=True)   
    department_id = Column(UUID(as_uuid=True), ForeignKey(DEPARTMENTS_ID, ondelete="CASCADE"), nullable=False, index=True)
    position_name = Column(String(100), nullable=True)  
    position_id = Column(UUID(as_uuid=True), ForeignKey(POSITIONS_ID, ondelete=SET_NULL), nullable=True, index=True)
    user_level = Column(String(10), nullable=False, index=True)  # 'L0', 'L1', 'L2'
    
    # Categories, Weights & Rules Payload
    categories = Column(JSON, nullable=False) 
    scope = Column(SQLEnum(FormulaScope), default=FormulaScope.DEPARTMENT, nullable=False)
    is_active = Column(Boolean, default=True, index=True)

    # Workflow & Versioning
    version = Column(Integer, default=1, nullable=False)
    status = Column(SQLEnum(FormulaStatus), default=FormulaStatus.DRAFT, nullable=False, index=True)
    parent_formula_id = Column(UUID(as_uuid=True), ForeignKey(KPI_FORMULAS_ID, ondelete=SET_NULL), nullable=True)
    
    effective_month = Column(String(7), nullable=True, index=True) # "YYYY-MM"
    created_by_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=SET_NULL), nullable=True)
    
    # Relationships
    department = relationship("Department")
    position = relationship("Position")
    creator = relationship("User", foreign_keys=[created_by_id])
    scoring_rules = relationship("KPIScoringRuleItem", back_populates="formula", cascade=ALL_DELETE_ORPHAN, lazy="selectin")
    parent_formula = relationship("KPIFormula", remote_side=lambda: [KPIFormula.id], foreign_keys=[parent_formula_id])

    __table_args__ = (
        Index('idx_formula_targeting', 'department_id', 'user_level', 'status', 'is_active'),
    )


class KPIPeriod(BaseModel):
    """Task 3.3: KPI Period Lifecycle Management"""
    __tablename__ = "kpi_periods"

    label = Column(String(100), nullable=False, index=True) # e.g., "November 2026", "Q3 2026"
    start_date = Column(DateTime(timezone=True), nullable=False, index=True)
    end_date = Column(DateTime(timezone=True), nullable=False, index=True)
    status = Column(SQLEnum(KPIPeriodStatus), default=KPIPeriodStatus.DRAFT, nullable=False, index=True)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)

    # Relationships
    records = relationship("KPIRecord", back_populates="period", cascade=ALL_DELETE_ORPHAN)


class KPIRecord(BaseModel):
    """Task 5.2 - 5.6: KPI Evaluation Aggregated Records"""
    __tablename__ = "kpi_records"
    
    user_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete="CASCADE"), nullable=False, index=True)
    department_id = Column(UUID(as_uuid=True), ForeignKey(DEPARTMENTS_ID, ondelete=SET_NULL), nullable=True, index=True)
    formula_id = Column(UUID(as_uuid=True), ForeignKey(KPI_FORMULAS_ID, ondelete=SET_NULL), nullable=True, index=True)
    kpi_period_id = Column(UUID(as_uuid=True), ForeignKey("kpi_periods.id", ondelete=SET_NULL), nullable=True, index=True)
    
    period_type = Column(SQLEnum(PeriodType), nullable=False, index=True)
    period_start = Column(DateTime(timezone=True), nullable=False, index=True) 
    period_end = Column(DateTime(timezone=True), nullable=False)   
    
    # Enterprise Scoring (Out of 5.0 Base & Percentage)
    raw_score = Column(Float, nullable=False, default=0.0)      
    weighted_score = Column(Float, nullable=False, default=0.0) 
    percentage = Column(Float, nullable=False, default=0.0)     
    
    status_bucket = Column(SQLEnum(KPIStatus), nullable=False, default=KPIStatus.GOOD, index=True)
    
    # Admin Overrides & Freezing
    is_frozen = Column(Boolean, default=False, index=True)
    is_overridden = Column(Boolean, default=False)
    override_reason = Column(Text, nullable=True)
    
    # Relationships
    user = relationship("User")
    department = relationship("Department")
    formula = relationship("KPIFormula")
    period = relationship("KPIPeriod", back_populates="records")
    action_plan = relationship("ActionPlan", back_populates="record", uselist=False, cascade=ALL_DELETE_ORPHAN)
    histories = relationship("KPIHistory", back_populates="record", cascade=ALL_DELETE_ORPHAN)

    @property
    def grade(self):
        """API/schema alias for status_bucket (KPIRecordResponse.grade)."""
        return self.status_bucket

    __table_args__ = (
        # User အလိုက် Monthly History အမြန်ဆုံးဆွဲရန်
        Index('idx_kpi_user_period_history', 'user_id', 'period_type', 'period_start'),
        # Department Ranking & Summary အမြန်ဆုံးဆွဲရန်
        Index('idx_kpi_dept_summary', 'department_id', 'period_type', 'period_start'),
    )


class KPIAssignment(BaseModel):
    """Task 3.4: Explicit KPI Assignment Linking"""
    __tablename__ = "kpi_assignments"

    department_id = Column(UUID(as_uuid=True), ForeignKey(DEPARTMENTS_ID, ondelete="CASCADE"), nullable=False)
    position_id = Column(UUID(as_uuid=True), ForeignKey(POSITIONS_ID, ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete="CASCADE"), nullable=False, index=True) 
    kpi_formula_id = Column(UUID(as_uuid=True), ForeignKey(KPI_FORMULAS_ID, ondelete="CASCADE"), nullable=False, index=True)
    kpi_period_id = Column(UUID(as_uuid=True), ForeignKey("kpi_periods.id", ondelete="CASCADE"), nullable=False, index=True)
    
    assigned_by_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=SET_NULL), nullable=True)
    assigned_at = Column(DateTime(timezone=True), nullable=False)
    is_deleted = Column(Boolean, default=False, nullable=False, index=True)

    # Relationships
    department = relationship("Department")
    position = relationship("Position")
    user = relationship("User", foreign_keys=[user_id])
    formula = relationship("KPIFormula")
    period = relationship("KPIPeriod")
    assigner = relationship("User", foreign_keys=[assigned_by_id])

    __table_args__ = (
        # 🌟 Data Integrity: ဝန်ထမ်းတစ်ဦးအား တူညီသော Formula ကို တစ်ကြိမ်ထက်ပို၍ မဆွဲထည့်စေရန် တားဆီးခြင်း
        UniqueConstraint('user_id', 'kpi_formula_id', 'kpi_period_id', name='uq_kpi_user_formula_period'),
        Index('idx_kpi_assignment_lookup', 'user_id', 'kpi_period_id'),
    )


class KPIScoringRuleItem(BaseModel):
    """Task 3.5: Normalized KPI Scoring & Deduction Rules Engine"""
    __tablename__ = "kpi_scoring_rule_items"

    formula_id = Column(UUID(as_uuid=True), ForeignKey(KPI_FORMULAS_ID, ondelete="CASCADE"), nullable=False, index=True)
    
    category_name = Column(String(255), nullable=False) # e.g., "Attendance & Punctuality"
    rule_name = Column(String(255), nullable=False)     # e.g., "ရုံးနောက်ကျခြင်း"
    measure_type = Column(String(50), nullable=False)   # "count" သို့မဟုတ် "percentage"
    
    operator = Column(String(20), nullable=False)       # ">=", "==", "<", "<=", ">"
    target_value = Column(Float, nullable=False)        # 1.0, 2.0, 95.0
    deduct_score = Column(Float, nullable=False)        # 1.0, 2.0, 4.0

    # Relationships
    formula = relationship("KPIFormula", back_populates="scoring_rules")


class ActionPlan(BaseModel):
    """Task 5.10: Performance Action Plans for Low Scores"""
    __tablename__ = "kpi_action_plans"

    kpi_record_id = Column(UUID(as_uuid=True), ForeignKey("kpi_records.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete="CASCADE"), nullable=False, index=True)
    
    description = Column(Text, nullable=False)
    target_completion_date = Column(DateTime(timezone=True), nullable=False)
    status = Column(SQLEnum(ActionPlanStatus), default=ActionPlanStatus.DRAFT, nullable=False, index=True)
    
    manager_feedback = Column(Text, nullable=True)
    reviewed_by_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=SET_NULL), nullable=True)
    reviewed_at = Column(DateTime(timezone=True), nullable=True)

    # Relationships
    record = relationship("KPIRecord", back_populates="action_plan")
    user = relationship("User", foreign_keys=[user_id])
    reviewer = relationship("User", foreign_keys=[reviewed_by_id])


class KPIHistory(BaseModel):
    """Task 5.7 & 5.15: KPI Calculation Audit Trail"""
    __tablename__ = "kpi_history"
    
    kpi_record_id = Column(UUID(as_uuid=True), ForeignKey("kpi_records.id", ondelete="CASCADE"), nullable=False, index=True)
    changed_by_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=SET_NULL), nullable=True)
    
    old_percentage = Column(Float, nullable=False)
    new_percentage = Column(Float, nullable=False)
    old_status = Column(SQLEnum(KPIStatus), nullable=False)
    new_status = Column(SQLEnum(KPIStatus), nullable=False)
    
    reason = Column(Text, nullable=False)
    
    # Relationships
    record = relationship("KPIRecord", back_populates="histories")
    changed_by = relationship("User", foreign_keys=[changed_by_id])


class FormulaAuditLog(BaseModel):
    """Enterprise Audit Trail: Formula Modifications"""
    __tablename__ = "formula_audit_logs"

    formula_id = Column(UUID(as_uuid=True), ForeignKey(KPI_FORMULAS_ID, ondelete="CASCADE"), nullable=False, index=True)
    action_by_id = Column(UUID(as_uuid=True), ForeignKey(USERS_ID, ondelete=SET_NULL), nullable=True)
    action = Column(String(50), nullable=False) # "SUBMITTED", "APPROVED", "REJECTED", "ACTIVATED"
    remarks = Column(Text, nullable=True)

    # Relationships
    formula = relationship("KPIFormula")
    action_by = relationship("User", foreign_keys=[action_by_id])


class KpiRule(BaseModel):
    """Task & KPI Category Mapping Matrix"""
    __tablename__ = "kpi_rules"

    department_id = Column(UUID(as_uuid=True), ForeignKey(DEPARTMENTS_ID, ondelete="CASCADE"), nullable=False, index=True)
    position_id = Column(UUID(as_uuid=True), ForeignKey(POSITIONS_ID, ondelete=SET_NULL), nullable=True, index=True)
    level = Column(String(10), nullable=False, index=True)        # 'L0', 'L1', 'L2'
    category_name = Column(String(150), nullable=False)           # 'Cash Book Report', 'Sales Target'
    weight = Column(Float, default=0.0, nullable=False)
    is_active = Column(Boolean, default=True, nullable=False, index=True)

    # Relationships
    department = relationship("Department")
    position = relationship("Position")

    __table_args__ = (
        Index('idx_kpi_rule_lookup', 'department_id', 'position_id', 'level', 'is_active'),
    )


class KpiCategory(BaseModel):
    """Simple lookup table for KPI categories referenced by tasks."""

    __tablename__ = "kpi_categories"

    id = Column(UUID(as_uuid=True), primary_key=True, default=func.gen_random_uuid())
    name = Column(String(100), nullable=False, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())