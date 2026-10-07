from uuid import UUID

from pydantic import BaseModel


class KPIAssignmentCreate(BaseModel):
    department_id: UUID
    position_id: UUID
    user_id: UUID
    kpi_formula_id: UUID
    kpi_period_id: UUID

class KPIBulkAssignmentCreate(BaseModel):
    department_id: UUID
    position_id: UUID
    user_ids: list[UUID]
    kpi_formula_id: UUID
    kpi_period_id: UUID