"""
Enterprise KPI Management System - Global System Constants
"""

# 🌟 1. USER ROLES & SYSTEM LEVELS
ROLE_SUPER_ADMIN = "Super Admin"
ROLE_ADMIN = "Admin"
ROLE_HR = "HR"
ROLE_DEPT_USER = "Department User"

SYSTEM_ROLES = [ROLE_SUPER_ADMIN, ROLE_ADMIN, ROLE_HR, ROLE_DEPT_USER]
ADMIN_ROLES = [ROLE_SUPER_ADMIN, ROLE_ADMIN]

USER_LEVEL_L0 = "L0"  # Department Head / Director
USER_LEVEL_L1 = "L1"  # Supervisor / Team Leader
USER_LEVEL_L2 = "L2"  # Operational Staff

USER_LEVELS = [USER_LEVEL_L0, USER_LEVEL_L1, USER_LEVEL_L2]

# 🌟 2. SECURITY & FILE UPLOAD RESTRICTIONS
MAX_EVIDENCE_FILE_SIZE = 10 * 1024 * 1024  # 10 MB for Task Evidences
MAX_SPREADSHEET_FILE_SIZE = 5 * 1024 * 1024  # 5 MB for KPI Excel/CSV

ALLOWED_EVIDENCE_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".csv", ".xlsx", ".doc", ".docx"}
ALLOWED_SPREADSHEET_EXTENSIONS = {".csv", ".xls", ".xlsx"}

ALLOWED_EVIDENCE_MIME_TYPES = [
    "application/pdf",
    "image/jpeg",
    "image/png",
    "text/csv",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.ms-excel",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
]

# 🌟 3. PASSWORD SECURITY POLICY CONSTANTS
MIN_PASSWORD_LENGTH = 12
MAX_FAILED_LOGIN_ATTEMPTS = 5
LOCKOUT_DURATION_MINUTES = 30
PASSWORD_HISTORY_LIMIT = 5

# 🌟 4. KPI SCORING MATRIX DEFAULTS
KPI_MAX_BASE_SCORE = 5.0
KPI_EXCELLENT_THRESHOLD = 90.0
KPI_VERY_GOOD_THRESHOLD = 80.0
KPI_GOOD_THRESHOLD = 60.0
KPI_FAIR_THRESHOLD = 40.0

# 🌟 5. RESTRICTED METRIC KEYWORDS (ADMIN ONLY)
ADMIN_ONLY_KEYWORDS = [
    "attendance", "punctuality", "compliance", "စည်းကမ်း", 
    "ခွင့်", "behavior", "discipline", "rule", "ရုံးစည်းကမ်း"
]

# 🌟 6. PAGINATION DEFAULTS
DEFAULT_PAGE_SKIP = 0
DEFAULT_PAGE_LIMIT = 50
MAX_PAGE_LIMIT = 500