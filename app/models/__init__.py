from app.database.connection import Base
from app.database.base import BaseModel

# 🌟 1. Users & Roles (Single Source of Truth)
from app.modules.users.models import User, Role, Position, Permission, RolePermission

# 🌟 2. Departments
from app.modules.departments.models import Department, UserDepartment

# 🌟 3. Tasks & Workflow Engine
from app.modules.tasks.models.task import Task
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_review import TaskReview
from app.modules.tasks.models.submission_batch import SubmissionBatch
from app.modules.tasks.models.approval_queue import ApprovalQueue
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.tasks.models.activity_timeline import ActivityTimeline

# 🌟 4. KPI Models
from app.modules.kpi.models import (
    KPIRecord, KPIFormula, KPILevelSetting, KPIPeriod, 
    KPIAssignment, KPIScoringRuleItem, ActionPlan, KpiRule, 
    KPIHistory, FormulaAuditLog
)

# 🌟 5. Targets & Notifications
from app.modules.targets.models import Target
from app.modules.notifications.models import Notification

# 🌟 6. Audit & Auth Logs
from app.models.audit_log import AuditLog
from app.modules.auth.models import PasswordHistory, LoginHistory, TokenBlacklist

# 🌟 7. System Settings & Logs
from app.models.system import SystemSetting, SystemEventLog