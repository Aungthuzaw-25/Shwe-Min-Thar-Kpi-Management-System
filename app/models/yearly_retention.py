from sqlalchemy import Column, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import BaseModel


class YearlyRetention(BaseModel):
    """Per-calendar-year backup gate for hard-deleting departed accounts.

    One row per year. KPI records, finished tasks and audit logs are
    kept permanently — this table never gates data removal. It holds
    only the BACKUP half of the account-deletion lock: an account of a
    person who left in a year may be hard-deleted after the year has
    ended, a backup for that year is confirmed here
    (``backup_confirmed_at`` / ``backup_confirmed_by``), AND a
    full-year download exists in ``report_downloads`` (type YEARLY
    with a window covering the whole year — monthly/custom downloads
    do not count). Each hard deletion itself is written to the audit
    log by the code that performs it.
    """

    __tablename__ = "yearly_retention"

    year = Column(Integer, unique=True, nullable=False, index=True)

    backup_confirmed_at = Column(DateTime, nullable=True)
    backup_confirmed_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
