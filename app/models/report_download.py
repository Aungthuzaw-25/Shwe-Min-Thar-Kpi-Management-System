import enum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import UUID

from app.database.base import BaseModel


class ReportType(str, enum.Enum):
    """Window kind of a downloaded report."""

    MONTHLY = "MONTHLY"
    QUARTERLY = "QUARTERLY"
    YEARLY = "YEARLY"
    CUSTOM = "CUSTOM"


class ReportDownload(BaseModel):
    """One row per report download (any window kind, any scope).

    ``period_start`` / ``period_end`` are the inclusive window dates;
    the download time is the row's ``created_at``. Scope is either
    company-wide (``is_company_wide``) or one department
    (``department_id``, with ``department_name`` as a readability
    snapshot). The year-end account-deletion lock only accepts a row
    that is YEARLY, covers the full calendar year, AND is
    company-wide — every other download never opens the lock.
    """

    __tablename__ = "report_downloads"

    report_type = Column(String(20), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    is_company_wide = Column(Boolean, nullable=False, default=False, server_default=text("false"))
    department_id = Column(
        UUID(as_uuid=True),
        ForeignKey("departments.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    department_name = Column(String(255), nullable=True)
    downloaded_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        Index(
            "idx_report_downloads_window",
            "report_type",
            "period_start",
            "period_end",
        ),
    )


class ReportGrantScope(str, enum.Enum):
    """Scope of a report-download grant."""

    COMPANY = "COMPANY"
    DEPARTMENT = "DEPARTMENT"


class ReportDownloadGrant(BaseModel):
    """A Super Admin's grant letting one user download reports.

    Scope is per grant: COMPANY (``department_id`` must be NULL) or
    DEPARTMENT (``department_id`` required) — enforced by a check
    constraint. A user can hold many grants and a department can
    appear in many users' grants. Download authorization checks this
    table (Super Admins themselves need no grant).

    Lifecycle: the grant time is ``created_at``. Revoke by setting
    ``is_active`` false and stamping ``revoked_at`` / ``revoked_by``.
    Grants die automatically with the grantee's account (``user_id``
    CASCADE) and with their department (``department_id`` CASCADE,
    which also keeps the scope check consistent); the grantor is only
    recorded (SET NULL) so deleting a grantor never revokes access.
    """

    __tablename__ = "report_download_grants"

    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    scope_type = Column(String(10), nullable=False)
    department_id = Column(
        UUID(as_uuid=True), ForeignKey("departments.id", ondelete="CASCADE"), nullable=True
    )
    granted_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    is_active = Column(Boolean, nullable=False, default=True, server_default=text("true"))
    revoked_at = Column(DateTime, nullable=True)
    revoked_by = Column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "(scope_type = 'COMPANY' AND department_id IS NULL) "
            "OR (scope_type = 'DEPARTMENT' AND department_id IS NOT NULL)",
            name="ck_report_download_grants_scope_department",
        ),
    )
