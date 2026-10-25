from sqlalchemy import Column, Date, String
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import BaseModel


class DepartedIdentity(BaseModel):
    """Identity of a departed employee whose account row was deleted.

    Created by the hard-delete flow (only when the user actually has
    KPI records or tasks worth keeping). The surviving ``kpi_records``
    and ``tasks`` rows point here through their
    ``departed_identity_id`` column, which is the exact grouping key
    for the "Departed Employee Records" views and for a later Relink
    if the person is re-hired — name/email snapshots alone cannot
    group reliably (names repeat, and an email can be re-registered
    by a different person after the original account is gone).

    ``original_user_id`` is the deleted account's id, stored as a
    plain value on purpose: it must survive the deletion, so it is
    NOT a foreign key. The display snapshots (name, department,
    resigned date) also live on the record/task rows themselves; this
    row is the identity those snapshots belong to.
    """

    __tablename__ = "departed_identities"

    original_user_id = Column(UUID(as_uuid=True), unique=True, nullable=False, index=True)
    employee_name = Column(String(255), nullable=False)
    email = Column(String(255), nullable=False, index=True)
    # Departments the person belonged to when the account was deleted
    # (names joined with ", "). A matching aid for Relink — the exact
    # per-record department lives on each record's own snapshot.
    department_name = Column(String(500), nullable=True)
    resigned_at = Column(Date, nullable=True)
