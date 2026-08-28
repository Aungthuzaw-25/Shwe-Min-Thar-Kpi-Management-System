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
import magic # SECURITY FIX: File Content (MIME) Type Validation
from datetime import datetime, timezone

from app.database.connection import get_db, AsyncSessionLocal # Background Worker DB Session
from app.core.dependencies import RoleChecker
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
@router.get("/export-template", summary="Download KPI Excel Template with Detailed Attendance & Target")
@limiter.limit("10/minute") 
async def export_kpi_template(
    request: Request, # Rate limiter 
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(admin_hr_checker)
):
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
@router.post("/monthly-data", status_code=status.HTTP_200_OK)
@limiter.limit("3/minute") # DoS Upload 
async def upload_monthly_kpi_data(
    request: Request, # Rate limiter 
    background_tasks: BackgroundTasks, # Heavy Calculation Non-blocking 
    file: UploadFile = File(...), 
    target_month: str = Form(..., description="Format: YYYY-MM (e.g., 2026-08)"), 
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(admin_hr_checker)
):
    try:
        # 1. SECURITY: Prevent Denial of Service (DoS) via huge file memory loads
        file.file.seek(0, 2)
        file_size = file.file.tell()
        await file.seek(0)
        
        if file_size > MAX_FILE_SIZE:
            raise ValueError("File too large. Maximum size allowed is 5MB to prevent memory overload.")

        # 2. SECURITY: Strict Extension Validation (Anti-Malware)
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError("Unsupported file format. Please upload a strictly formatted .csv or .xlsx file.")

        # Safe to read into memory now
        contents = await file.read()
        
        # SECURITY FIX: File Content (MIME) Type Spoofing Detection
        mime_type = magic.from_buffer(contents, mime=True)
        if not any(mime in mime_type.lower() for mime in ["csv", "excel", "sheet", "zip", "text", "octet-stream"]):
            raise ValueError(f"Security Alert: File content signature ({mime_type}) does not match a valid spreadsheet.")
        
        # PERFORMANCE FIX: Offload pandas parsing to threadpool
        def parse_file(content, extension):
            if extension == '.csv':
                return pd.read_csv(io.BytesIO(content))
            else:
                return pd.read_excel(io.BytesIO(content))
                
        df = await run_in_threadpool(parse_file, contents, ext)
        
        # 3. Data Cleaning: Strip trailing whitespaces
        df.columns = df.columns.str.strip()
        
        if 'Email' not in df.columns:
            raise ValueError("The uploaded file is missing the strictly required 'Email' column.")

        # 4. Time Boundary Calculation (Timezone Aware)
        try:
            year, month = map(int, target_month.split('-'))
            period_start = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
            last_day = calendar.monthrange(year, month)[1]
            period_end = datetime(year, month, last_day, 23, 59, 59, tzinfo=timezone.utc)
        except ValueError:
            raise ValueError("Invalid target_month format. Must be YYYY-MM (e.g., 2026-08).")

        error_logs = []
        valid_rows = []
        
        # 🌟 PERFORMANCE FIX (DBA): N+1 Query Optimization
        # Extract all emails from the dataframe and query the database exactly once.
        extracted_emails = df['Email'].dropna().astype(str).str.strip().tolist()
        
        stmt = select(User.id, User.email).where(
            User.email.in_(extracted_emails), 
            User.is_active == True, 
            User.is_deleted == False
        )
        user_records = (await db.execute(stmt)).all()
        # Map email to user_id for O(1) memory lookup
        email_to_user_id = {email: uid for uid, email in user_records}

        ignore_cols = ['email', 'employee name', 'name', 'department', 'id', 'no', 'no.', 'sr', 'sr.']
        
        for index, row in df.iterrows():
            if pd.isna(row.get('Email')) or str(row['Email']).strip() == "":
                continue
                
            email = str(row['Email']).strip()
            
            # 🌟 FAST O(1) Memory Lookup instead of awaiting DB on every row
            user_id = email_to_user_id.get(email)
            
            if not user_id:
                error_logs.append(f"Row {index + 2}: Active user with email '{email}' not found.")
                continue

            uploaded_task_data = {}
            for col in df.columns:
                col_name = str(col).strip()
                if col_name.lower() not in ignore_cols:
                    val = row[col]
                    if pd.notna(val) and str(val).strip() != "":
                        val_str = str(val).strip()
                        # Safe parsing: Treat '-', 'N/A' as 0.0
                        if val_str in ['-', 'N/A', 'NA', 'None', 'null']:
                            uploaded_task_data[col_name] = 0.0
                        else:
                            try:
                                uploaded_task_data[col_name] = float(val_str)
                            except ValueError:
                                error_logs.append(f"Row {index + 2}: Invalid metric number '{val_str}' in column '{col_name}'.")

            # Thread-safe Payload (User Object User ID Pass 
            valid_rows.append({"user_id": user_id, "task_data": uploaded_task_data})

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