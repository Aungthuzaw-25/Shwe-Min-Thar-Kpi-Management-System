from pydantic import BaseModel, Field, ConfigDict
from datetime import datetime
from typing import Optional
from uuid import UUID
from app.modules.kpi.models import KPIPeriodStatus

class KPIPeriodCreate(BaseModel):
    label: str = Field(..., min_length=3, max_length=100, description="Name of the period, e.g., November 2026")
    start_date: datetime = Field(..., description="Start boundary of the KPI cycle")
    end_date: datetime = Field(..., description="End boundary of the KPI cycle")

class KPIPeriodResponse(KPIPeriodCreate):
    id: UUID
    status: KPIPeriodStatus

    # 🌟 Pydantic V2 အတွက် အကောင်းဆုံး Practice
    model_config = ConfigDict(from_attributes=True)