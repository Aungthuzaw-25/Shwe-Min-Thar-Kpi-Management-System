from app.database.base import BaseModel
from app.database.connection import Base

# 🌟 6. Audit & Auth Logs
from app.models.audit_log import AuditLog
from app.models.departed_identity import DepartedIdentity
from app.models.report_download import (
    ReportDownload,
    ReportDownloadGrant,
    ReportGrantScope,
    ReportType,
)
from app.models.yearly_retention import YearlyRetention

# 🌟 7. System Settings & Logs
from app.models.system import SystemEventLog, SystemSetting
from app.modules.auth.models import AuthSession, LoginHistory, PasswordHistory, TokenBlacklist

# 🌟 2. Departments
from app.modules.departments.models import Department, UserDepartment

# 🌟 4. KPI Models
from app.modules.kpi.models import (
    ActionPlan,
    FormulaAuditLog,
    KPIAssignment,
    KPIFormula,
    KPIHistory,
    KPILevelSetting,
    KPIPeriod,
    KPIRecord,
    KpiRule,
    KPIScoringRuleItem,
)
from app.modules.notifications.models import Notification

# 🌟 5. Targets & Notifications
from app.modules.targets.models import Target
from app.modules.tasks.models.activity_timeline import ActivityTimeline
from app.modules.tasks.models.approval_queue import ApprovalQueue
from app.modules.tasks.models.submission_batch import SubmissionBatch

# 🌟 3. Tasks & Workflow Engine
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.tasks.models.task_review import TaskReview

# 🌟 1. Users & Roles (Single Source of Truth)
from app.modules.users.models import Permission, Position, Role, RolePermission, User

__all__ = [
    'BaseModel',
    'Base',
    'AuditLog',
    'DepartedIdentity',
    'ReportDownload',
    'ReportDownloadGrant',
    'ReportGrantScope',
    'ReportType',
    'YearlyRetention',
    'SystemEventLog',
    'SystemSetting',
    'AuthSession',
    'LoginHistory',
    'PasswordHistory',
    'TokenBlacklist',
    'Department',
    'UserDepartment',
    'ActionPlan',
    'FormulaAuditLog',
    'KPIAssignment',
    'KPIFormula',
    'KPIHistory',
    'KPILevelSetting',
    'KPIPeriod',
    'KPIRecord',
    'KpiRule',
    'KPIScoringRuleItem',
    'Notification',
    'Target',
    'ActivityTimeline',
    'ApprovalQueue',
    'SubmissionBatch',
    'Task',
    'TaskEvidence',
    'TaskHistory',
    'TaskReview',
    'Permission',
    'Position',
    'Role',
    'RolePermission',
    'User',
]
