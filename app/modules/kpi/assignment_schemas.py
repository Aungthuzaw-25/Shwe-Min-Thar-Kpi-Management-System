from pydantic import BaseModel
from typing import List
from uuid import UUID

class KPIAssignmentCreate(BaseModel):
    department_id: UUID
    position_id: UUID
    user_id: UUID
    kpi_formula_id: UUID
    kpi_period_id: UUID

class KPIBulkAssignmentCreate(BaseModel):
    department_id: UUID
    position_id: UUID
    user_ids: List[UUID]
    kpi_formula_id: UUID
    kpi_period_id: UUID