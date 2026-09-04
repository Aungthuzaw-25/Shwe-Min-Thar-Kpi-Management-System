from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Form, Request, BackgroundTasks
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from fastapi.concurrency import run_in_threadpool
import pandas as pd
import io
import calendar
import os
import mimetypes
from datetime import datetime, timezone

try:
    import magic  # Optional dependency for MIME validation.
except ImportError:  # pragma: no cover - libmagic may be absent in runtime image.
    magic = None

from app.database.connection import get_db, AsyncSessionLocal # Background Worker DB Session
from app.core.dependencies import RoleChecker, enforce_permission
from app.modules.users.models import User
from app.modules.departments.models import UserDepartment
from app.modules.kpi.models import PeriodType
from app.modules.kpi.engine import KPICalculationEngine

# RATE LIMITER IMPORTS
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
router = APIRouter(prefix="/kpi-uploads", tags=["KPI Excel Uploads"])
admin_hr_checker = RoleChecker(['Super Admin', 'System Super Admin', 'Admin', 'HR'])

# SECURITY: Anti-Malware & Anti-DoS Constants
MAX_FILE_SIZE = 5 * 1024 * 1024  # 5 MB Limit for Spreadsheets
ALLOWED_EXTENSIONS = {".csv", ".xls", ".xlsx"}
ALLOWED_MIME_TYPES = [
    "text/csv",
    "text/plain",
    "application/vnd.ms-excel",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/octet-stream", # Excel 
    "application/zip" # xlsx XML 
]


def _detect_mime_type(file_bytes: bytes, filename: str | None = None) -> str:
    """Fallback MIME detection if libmagic is missing."""
    if magic is not None:
        try:
            detected = magic.from_buffer(file_bytes, mime=True)
            if detected:
                return detected
        except Exception:
            pass

    if filename:
        guessed = mimetypes.guess_type(filename)[0]
        if guessed:
            return guessed

    if file_bytes.startswith(b"PK"):
        return "application/zip"
    if file_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if file_bytes.startswith(b"%PDF"):
        return "application/pdf"
    return "application/octet-stream"


async def _read_upload_dataframe(file: UploadFile) -> pd.DataFrame:
    file.file.seek(0, 2)
    file_size = file.file.tell()
    await file.seek(0)
    if file_size > MAX_FILE_SIZE:
        raise ValueError("File too large. Maximum size allowed is 5MB to prevent memory overload.")
    extension = os.path.splitext(file.filename)[1].lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Unsupported file format. Please upload a strictly formatted .csv or .xlsx file.")
    contents = await file.read()
    mime_type = _detect_mime_type(contents, file.filename)
    if not any(mime in mime_type.lower() for mime in ["csv", "excel", "sheet", "zip", "text", "octet-stream"]):
        raise ValueError(f"Security Alert: File content signature ({mime_type}) does not match a valid spreadsheet.")

    def parse_file():
        source = io.BytesIO(contents)
        return pd.read_csv(source) if extension == ".csv" else pd.read_excel(source)

    dataframe = await run_in_threadpool(parse_file)
    dataframe.columns = dataframe.columns.str.strip()
    if "Email" not in dataframe.columns:
        raise ValueError("The uploaded file is missing the strictly required 'Email' column.")
    return dataframe


async def _build_upload_rows(df: pd.DataFrame, db: AsyncSession):
    emails = df["Email"].dropna().astype(str).str.strip().tolist()
    statement = select(User.id, User.email).where(
        User.email.in_(emails), User.is_active == True, User.is_deleted == False
    )
    user_records = (await db.execute(statement)).all()
    email_to_user_id = {email: user_id for user_id, email in user_records}
    ignored_columns = {"email", "employee name", "name", "department", "id", "no", "no.", "sr", "sr."}
    errors, valid_rows = [], []
    for index, row in df.iterrows():
        email = str(row["Email"]).strip() if pd.notna(row.get("Email")) else ""
        if not email:
            continue
        user_id = email_to_user_id.get(email)
        if not user_id:
            errors.append(f"Row {index + 2}: Active user with email '{email}' not found.")
            continue
        task_data = {}
        for column in df.columns:
            if str(column).strip().lower() in ignored_columns:
                continue
            value = row[column]
            if pd.isna(value) or not str(value).strip():
                continue
            value_text = str(value).strip()
            if value_text in {"-", "N/A", "NA", "None", "null"}:
                task_data[str(column).strip()] = 0.0
                continue
            try:
                task_data[str(column).strip()] = float(value_text)
            except ValueError:
                errors.append(f"Row {index + 2}: Invalid metric number '{value_text}' in column '{column}'.")
        valid_rows.append({"user_id": user_id, "task_data": task_data})
    return valid_rows, errors

# ==============================================================================
# BACKGROUND WORKER FUNCTION (Non-Blocking Heavy Calculation Engine)
# ==============================================================================
async def process_kpi_in_background(valid_rows_data: list, period_start: datetime, period_end: datetime):
    """
    API Response (Background) DB Session Worker
    """
    async with AsyncSessionLocal() as bg_db:
        # 🌟 FAULT TOLERANCE FIX: Try-Except moved inside the loop. 
        # If one user fails, it won't crash the calculation for the remaining users.
        for item in valid_rows_data:
            try:
                user_id = item["user_id"]
                task_data = item["task_data"]
                
                # Background Session User 
                stmt = select(User).options(
                    selectinload(User.user_departments),
                    selectinload(User.position)
                ).where(User.id == user_id, User.is_active == True)
                
                user = (await bg_db.execute(stmt)).scalars().first()
                if user:
                    await KPICalculationEngine.execute_kpi_calculation(
                        db=bg_db,
                        user=user,
                        period_start=period_start,
                        period_end=period_end,
                        period_type=PeriodType.MONTHLY,
                        task_data=task_data
                    )
            except Exception as e:
                print(f"[KPI Background Processing Error for User {item.get('user_id')}]: {str(e)}")

# ==============================================================================
# 1. EXPORT TEMPLATE API (Admin Template )
# ==============================================================================
@router.get(
    "/export-template",
    summary="Download KPI Excel Template with Detailed Attendance & Target",
    responses={status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "Template generation error"}},
)
@limiter.limit("10/minute") 
async def export_kpi_template(
    request: Request, # Rate limiter 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_hr_checker)
):
    enforce_permission(current_user, "report.export")
    try:
        # Database Active 
        stmt = select(User).options(
            selectinload(User.user_departments).selectinload(UserDepartment.department)
        ).where(User.is_active == True, User.is_deleted == False)
        
        users = (await db.execute(stmt)).scalars().all()
        
        rows = []
        for u in users:
            dept_name = "N/A"
            if u.user_departments and getattr(u.user_departments[0], 'department', None):
                dept_name = u.user_departments[0].department.name
                
            rows.append({
                "Email": u.email,
                "Employee Name": f"{u.first_name} {u.last_name}",
                "Department": dept_name,
                "Late Attendance (Times)": 0,           # 
                "Unauthorized Leave / AWOL (Days)": 0,  # 
                "Authorized Leave (Days)": 0,           # 
                "Compliance Violation (Times)": 0,      # 
                "Actual Target Value": 0.0              # Target 
            })
            
        df = pd.DataFrame(rows)
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, sheet_name='KPI_Data', index=False)
            
            # Column (Width) 
            worksheet = writer.sheets['KPI_Data']
            worksheet.column_dimensions['A'].width = 28  # Email
            worksheet.column_dimensions['B'].width = 25  # Name
            worksheet.column_dimensions['C'].width = 22  # Department
            worksheet.column_dimensions['D'].width = 25  # Late
            worksheet.column_dimensions['E'].width = 32  # AWOL
            worksheet.column_dimensions['F'].width = 25  # Auth Leave
            worksheet.column_dimensions['G'].width = 30  # Compliance
            worksheet.column_dimensions['H'].width = 22  # Target
            
        output.seek(0)
        
        headers = {
            'Content-Disposition': 'attachment; filename="KPI_Monthly_Detailed_Template.xlsx"'
        }
        return StreamingResponse(
            output, 
            headers=headers, 
            media_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate template: {str(e)}")

# ==============================================================================
# 2. IMPORT DATA API (Admin Upload )
# ==============================================================================
@router.post(
    "/monthly-data",
    status_code=status.HTTP_200_OK,
    responses={status.HTTP_500_INTERNAL_SERVER_ERROR: {"description": "System processing error"}},
)
@limiter.limit("3/minute") # DoS Upload 
async def upload_monthly_kpi_data(
    request: Request, # Rate limiter 
    background_tasks: BackgroundTasks, # Heavy Calculation Non-blocking 
    file: UploadFile = File(...), 
    target_month: str = Form(..., description="Format: YYYY-MM (e.g., 2026-08)"), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker)
):
    enforce_permission(current_user, "kpi.create")
    try:
        df = await _read_upload_dataframe(file)
        try:
            year, month = map(int, target_month.split('-'))
            period_start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
            last_day = calendar.monthrange(year, month)[1]
            period_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid target_month format. Must be YYYY-MM (e.g., 2026-08).")

        valid_rows, error_logs = await _build_upload_rows(df, db)

        if error_logs:
            error_message = " | ".join(error_logs[:5])
            if len(error_logs) > 5:
                error_message += f" | ... and {len(error_logs) - 5} more errors."
            raise ValueError(error_message)

        # 5. PERFORMANCE & SCALABILITY FIX: Heavy Calculation Background 
        background_tasks.add_task(process_kpi_in_background, valid_rows, period_start, period_end)
        
        return {
            "message": "Excel data uploaded successfully. KPI calculations are being processed in the background.",
            "processed_count": len(valid_rows),
            "status": "processing"
        }
        
    except ValueError as ve:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(ve))
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"System Processing Error: {str(e)}")