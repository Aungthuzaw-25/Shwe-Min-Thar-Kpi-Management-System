import hashlib
import json
import logging
import os
from datetime import UTC, datetime  # 🌟 FIX: Added timezone for late calculation
from io import BytesIO
from uuid import UUID
from zipfile import BadZipFile, ZipFile

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import joinedload, selectinload

try:
    import magic  # Optional dependency for strict MIME sniffing.
except ImportError:  # pragma: no cover - environment may not include libmagic.
    magic = None

from app.core.dependencies import GLOBAL_SCOPE_ROLES, enforce_permission, get_current_user
from app.core.timezone import APPLICATION_TIMEZONE
from app.database.connection import get_db
from app.modules.departments.models import UserDepartment
from app.modules.kpi.category_service import get_eligible_kpi_categories_for_user
from app.modules.kpi.engine import trigger_kpi_recalculation
from app.modules.kpi.models import KPIFormula
from app.modules.notifications.repo import create_notification
from app.modules.tasks import repo as task_repo
from app.modules.tasks import service as task_service
from app.modules.tasks import workflow_repo
from app.modules.tasks.models.approval_queue import ApprovalQueue
from app.modules.tasks.models.submission_batch import SubmissionBatch
from app.modules.tasks.models.task import Task, TaskStatus
from app.modules.tasks.models.task_evidence import TaskEvidence
from app.modules.tasks.models.task_history import TaskHistory
from app.modules.tasks.models.task_review import TaskReview
from app.modules.tasks.schemas import (
    BulkTaskCreate,
    TaskCreate,
    TaskEvidenceResponse,
    TaskResponse,
    TaskReviewInput,
)
from app.modules.tasks.workflow_schemas import (
    BatchReviewRequest,
    BatchSubmitRequest,
    BatchSubmitResponse,
    QueueItemResponse,
)
from app.modules.users.avatar import avatar_color_index, avatar_initials, avatar_url
from app.modules.users.models import Position, User
from app.services.activity_service import record_activity
from app.services.storage_service import storage_service

router = APIRouter()
logger = logging.getLogger(__name__)
app_timezone = APPLICATION_TIMEZONE
TASK_APPROVE_PERMISSION = "tasks.approve"
LEVEL_PREFIX = "LEVEL "
SUPER_ADMIN_ROLE_NAME = "Super Admin"
LEVEL_ONE_ROLE_NAME = "Level 1"
LEVEL_ZERO_ROLE_NAME = "Level 0"
ERR_TASK_NOT_FOUND = "Task not found."
ROLE_DEPARTMENT_HEAD = "Department Head"
EXT_DOCX = ".docx"
EXT_XLSX = ".xlsx"
MIME_JPEG = "image/jpeg"
MIME_CSV = "text/csv"
MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


async def _authorized_task(
    db: AsyncSession,
    task_id: UUID,
    current_user: User,
    *,
    allow_workflow_scope: bool = False,
) -> Task:
    """Load a task only when the caller has an ownership or organizational relationship."""
    stmt = select(Task).options(
        joinedload(Task.owner).selectinload(User.user_departments),
        selectinload(Task.reviews),
    ).where(Task.id == task_id)
    task = (await db.execute(stmt)).unique().scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail=ERR_TASK_NOT_FOUND)

    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return task
    if current_user.id in {task.owner_id, task.assigner_id}:
        return task
    if any(review.reviewer_id == current_user.id for review in task.reviews):
        return task

    owner_departments = {
        str(link.department_id)
        for link in (task.owner.user_departments or [])
        if not link.is_deleted
    }
    current_links = await db.execute(
        select(UserDepartment.department_id).where(
            UserDepartment.user_id == current_user.id,
            UserDepartment.is_deleted.is_(False),
        )
    )
    current_departments = {str(value) for value in current_links.scalars().all()}
    allowed_department_roles = {"Admin", ROLE_DEPARTMENT_HEAD}
    if allow_workflow_scope:
        allowed_department_roles.add("Department User")
    if (
        current_user.role
        and current_user.role.name in allowed_department_roles
        and owner_departments.intersection(current_departments)
    ):
        return task
    raise HTTPException(status_code=404, detail=ERR_TASK_NOT_FOUND)


async def _authorize_workflow_actor(
    db: AsyncSession,
    task_id: UUID,
    current_user: User,
    required_level: str,
    permission: str = TASK_APPROVE_PERMISSION,
) -> Task:
    """Authorize a workflow mutation against the task's owner and reviewer scope."""
    enforce_permission(current_user, permission)
    task = await _authorized_task(
        db,
        task_id,
        current_user,
        allow_workflow_scope=True,
    )

    role_name = current_user.role.name if current_user.role else ""
    if role_name in GLOBAL_SCOPE_ROLES:
        return task

    membership = (
        await db.execute(
            select(UserDepartment).where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted.is_(False),
            )
        )
    ).scalars().first()
    owner_department_ids = {
        str(link.department_id)
        for link in (task.owner.user_departments or [])
        if not link.is_deleted
    }
    if (
        not membership
        or str(membership.department_id) not in owner_department_ids
        or str(membership.user_level).upper().replace(LEVEL_PREFIX, "") != required_level
    ):
        raise HTTPException(status_code=403, detail="You are not authorized for this workflow stage.")
    return task

# 🌟 ADMIN ONLY KEYWORDS 
ADMIN_ONLY_KEYWORDS = [
    "attendance", "punctuality", "compliance", "စည်းကမ်း", 
    "ခွင့်", "behavior", "discipline", "rule", "ရုံးစည်းကမ်း"
]

# 🌟 SECURITY: Anti-Malware & Anti-DoS Constants
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB
ALLOWED_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png", ".csv", EXT_XLSX, ".doc", EXT_DOCX}

# 🌟 SECURITY: Allowed MIME Types for Strict File Content Validation
ALLOWED_MIME_TYPES = [
    "application/pdf", 
    MIME_JPEG,
    "image/png", 
    MIME_CSV,
    MIME_XLSX,
    "application/vnd.ms-excel", 
    "application/msword", 
    MIME_DOCX
]


def _office_archive_mime(file_bytes: bytes, extension: str) -> str | None:
    if extension not in {EXT_XLSX, EXT_DOCX}:
        return None
    try:
        with ZipFile(BytesIO(file_bytes)) as archive:
            names = set(archive.namelist())
    except BadZipFile as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Office document archive is invalid.",
        ) from exc
    if "[Content_Types].xml" not in names:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Office document archive is invalid.",
        )
    if extension == EXT_XLSX and "xl/workbook.xml" in names:
        return MIME_XLSX
    if extension == EXT_DOCX and "word/document.xml" in names:
        return MIME_DOCX
    return None


def _mime_matches_extension(extension: str, detected: str) -> bool:
    extension_mimes = {
        ".pdf": {"application/pdf"},
        ".jpg": {MIME_JPEG},
        ".jpeg": {MIME_JPEG},
        ".png": {"image/png"},
        ".csv": {MIME_CSV, "text/plain", "application/csv"},
        EXT_XLSX: {MIME_XLSX},
        ".doc": {"application/msword"},
        EXT_DOCX: {MIME_DOCX},
    }
    return detected in extension_mimes.get(extension, set())


def _detect_mime_type(file_bytes: bytes, filename: str | None = None) -> str:
    """Require content sniffing and verify Office archives against their structure."""
    if magic is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secure file inspection is unavailable.",
        )
    try:
        detected = magic.from_buffer(file_bytes, mime=True)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unable to inspect file content safely.",
        ) from exc
    if not detected:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Unable to determine the file content type.",
        )

    extension = os.path.splitext(filename or "")[1].lower()
    if detected == "application/zip":
        archive_mime = _office_archive_mime(file_bytes, extension)
        if archive_mime:
            return archive_mime

    if not _mime_matches_extension(extension, detected):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="File content does not match its extension.",
        )
    return MIME_CSV if extension == ".csv" else detected


async def _get_user_allowed_categories(db: AsyncSession, user_id: UUID):
    return await get_eligible_kpi_categories_for_user(db, user_id)


async def _validate_assigned_task_payload(db: AsyncSession, current_user: User, assignee_id: UUID, category: str, due_date, grace_period_days: int):
    if not assignee_id:
        raise HTTPException(status_code=400, detail="Assigned task အတွက် Task Receiver မဖြစ်မနေ ရွေးရမည်။")
    if not due_date:
        raise HTTPException(status_code=400, detail="Assigned task အတွက် Due Date မဖြစ်မနေ ရွေးရမည်။")
    if grace_period_days is None or grace_period_days < 0:
        raise HTTPException(status_code=400, detail="Allowed Late Days သည် 0 ထက်မနည်း ဖြစ်ရမည်။")

    assignee = await db.get(User, assignee_id)
    if not assignee or not assignee.is_active:
        raise HTTPException(status_code=404, detail="Selected assignee is no longer active.")

    if str(assignee.id) == str(current_user.id):
        raise HTTPException(status_code=400, detail="Self-assignment is not allowed; use your personal task flow instead.")

    current_user_department = (await db.execute(select(UserDepartment).where(UserDepartment.user_id == current_user.id))).scalars().first()
    assignee_department = (await db.execute(select(UserDepartment).where(UserDepartment.user_id == assignee_id))).scalars().first()

    if not current_user_department or not assignee_department:
        raise HTTPException(status_code=403, detail="Department configuration error: assignment hierarchy could not be validated.")
    if current_user_department.department_id != assignee_department.department_id:
        raise HTTPException(status_code=403, detail="Cross-department task assignment is not allowed.")

    current_level = str(current_user_department.user_level).upper().replace(LEVEL_PREFIX, "")
    target_level = str(assignee_department.user_level).upper().replace(LEVEL_PREFIX, "")

    allowed_hierarchy = {
        "L0": {"L1", "L2"},
        "L1": {"L2"},
        "L2": set(),
    }
    if current_level not in allowed_hierarchy or target_level not in allowed_hierarchy[current_level]:
        raise HTTPException(status_code=403, detail="This assignment is outside the allowed reporting hierarchy.")

    allowed_categories = await get_eligible_kpi_categories_for_user(db, assignee_id)
    if category not in allowed_categories:
        raise HTTPException(status_code=403, detail=f"Selected category '{category}' is not allowed for the chosen assignee.")

    return assignee


@router.get('/categories/available', tags=["Tasks"])
async def get_available_task_categories(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    categories = {
        category
        for category in await _get_user_allowed_categories(db, current_user.id)
        if not any(keyword in category.lower() for keyword in ADMIN_ONLY_KEYWORDS)
    }
    if not categories:
        return {"categories": [], "message": "No manual KPI categories available for your profile."}

    return {"categories": sorted(categories)}


@router.get('/categories/for-user/{user_id}', tags=["Tasks"], responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def get_task_categories_for_user(
    user_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return only active KPI categories configured for the selected assignee."""
    assignee_stmt = select(User).where(User.id == user_id, User.is_active == True)
    assignee = (await db.execute(assignee_stmt)).scalars().first()
    if not assignee:
        raise HTTPException(status_code=404, detail="Selected assignee was not found or is inactive.")

    # Reuse the same reporting hierarchy enforced during task creation.
    current_ud = (await db.execute(
        select(UserDepartment).where(UserDepartment.user_id == current_user.id)
    )).scalars().first()
    assignee_ud = (await db.execute(
        select(UserDepartment).where(UserDepartment.user_id == user_id)
    )).scalars().first()
    if not current_ud or not assignee_ud:
        raise HTTPException(status_code=403, detail="Department configuration error.")
    if current_ud.department_id != assignee_ud.department_id:
        raise HTTPException(status_code=403, detail="Cross-department task assignment is not allowed.")

    current_level = str(current_ud.user_level).upper().replace(LEVEL_PREFIX, "")
    target_level = str(assignee_ud.user_level).upper().replace(LEVEL_PREFIX, "")
    allowed_hierarchy = {"L0": {"L1", "L2"}, "L1": {"L2"}, "L2": set()}
    if current_user.role and current_user.role.name not in GLOBAL_SCOPE_ROLES:
        if target_level not in allowed_hierarchy.get(current_level, set()):
            raise HTTPException(status_code=403, detail="This assignment is outside the allowed reporting hierarchy.")

    return {"categories": sorted(await get_eligible_kpi_categories_for_user(db, user_id))}

@router.get('/batch/{batch_id}/deductions', tags=["Tasks"], responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def get_batch_deductions(
    batch_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    batch = await db.get(SubmissionBatch, batch_id)
    if not batch:
        raise HTTPException(status_code=404, detail="Submission batch not found.")

    target_user = await db.get(User, batch.submitter_id)
    if not target_user:
        raise HTTPException(status_code=404, detail="Batch submitter not found.")

    allowed_roles = {SUPER_ADMIN_ROLE_NAME, "Admin", "HR", ROLE_DEPARTMENT_HEAD}
    role_name = current_user.role.name if current_user.role else ""
    if target_user.id != current_user.id and role_name not in allowed_roles:
        raise HTTPException(status_code=403, detail="Not authorized to view batch deductions.")

    ud_stmt = select(UserDepartment).where(
        UserDepartment.user_id == target_user.id,
        UserDepartment.is_deleted.is_(False),
    )
    ud = (await db.execute(ud_stmt)).scalars().first()
    if not ud:
        return {"deductions": []}
    if role_name == ROLE_DEPARTMENT_HEAD and target_user.id != current_user.id:
        caller_departments = set(
            (
                await db.execute(
                    select(UserDepartment.department_id).where(
                        UserDepartment.user_id == current_user.id,
                        UserDepartment.is_deleted.is_(False),
                    )
                )
            ).scalars().all()
        )
        if ud.department_id not in caller_departments:
            raise HTTPException(
                status_code=403,
                detail="Department Heads can only view deductions for their department.",
            )

    pos_obj = await db.get(Position, target_user.position_id) if target_user.position_id else None
    user_pos_name = pos_obj.name.strip().lower() if pos_obj and pos_obj.name else ""

    f_stmt = select(KPIFormula).where(
        KPIFormula.department_id == ud.department_id,
        KPIFormula.user_level == ud.user_level,
        KPIFormula.is_active == True,
    )
    formulas = (await db.execute(f_stmt)).scalars().all()

    target_formulas = _matching_batch_formulas(
        formulas, target_user.position_id, user_pos_name
    )
    deductions = _batch_formula_deductions(target_formulas)
    return {"deductions": sorted(deductions)}


def _matching_batch_formulas(formulas, position_id, user_pos_name):
    target_formulas = [
        formula
        for formula in formulas
        if (
            formula.position_id
            and str(formula.position_id) == str(position_id)
        )
        or (
            formula.position_name
            and formula.position_name.strip().lower() == user_pos_name
        )
    ]
    if target_formulas:
        return target_formulas
    return [
        formula
        for formula in formulas
        if not formula.position_id and not formula.position_name
    ]


def _batch_formula_deductions(formulas):
    deductions = set()
    for formula in formulas:
        raw_categories = formula.categories
        if isinstance(raw_categories, str):
            try:
                raw_categories = json.loads(raw_categories)
            except json.JSONDecodeError:
                raw_categories = []
        if not isinstance(raw_categories, list):
            continue
        deductions.update(_category_deductions(raw_categories))
    return deductions


def _category_deductions(categories):
    deductions = set()
    for category in categories:
        if not isinstance(category, dict):
            continue
        category_name = category.get("name", "")
        if any(keyword in category_name.lower() for keyword in ADMIN_ONLY_KEYWORDS):
            continue
        category_deductions = category.get("deductions", [])
        if not isinstance(category_deductions, list):
            continue
        for deduction in category_deductions:
            if isinstance(deduction, dict) and deduction.get("name"):
                deductions.add(deduction["name"])
    return deductions

@router.post('', response_model=TaskResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def create_new_task(payload: TaskCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """ 🌟 L2 များ ကိုယ်တိုင် Task ဖန်တီးသော Endpoint (Due Date / Late Penalty မလိုပါ) """
    enforce_permission(current_user, "tasks.create")
    try:
        ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
        ud = (await db.execute(ud_stmt)).scalars().first()

        if not ud or not current_user.position_id:
            raise HTTPException(status_code=403, detail="Security Violation: User lacks valid Department or Position configuration.")

        target_user_id = payload.owner_id or current_user.id
        if str(target_user_id) != str(current_user.id):
            await _validate_assigned_task_payload(
                db=db,
                current_user=current_user,
                assignee_id=target_user_id,
                category=payload.category,
                due_date=payload.due_date,
                grace_period_days=payload.grace_period_days or 0,
            )
        valid_categories = await get_eligible_kpi_categories_for_user(db, target_user_id)
        valid_categories = {
            category for category in valid_categories
            if not any(keyword in category.lower() for keyword in ADMIN_ONLY_KEYWORDS)
        }

        if payload.category not in valid_categories:
            raise HTTPException(status_code=403, detail=f"Security Violation: Category '{payload.category}' is not assigned to your specific Position & Level.")

        task_data = payload.model_dump(exclude_unset=True)
        task_data['assigner_id'] = current_user.id
        task_data['owner_id'] = target_user_id
        task_data['status'] = TaskStatus.DRAFT
        
        task = await task_repo.create_task(db, task_data)
        db.add(TaskHistory(task_id=task.id, changed_by=current_user.id, action="CREATED", old_value="NONE", new_value=TaskStatus.DRAFT.value, remarks="Task အသစ်ဖန်တီးခြင်း"))
        record_activity(
            db,
            actor_id=current_user.id,
            action="TASK_ASSIGNED" if target_user_id != current_user.id else "TASK_CREATED",
            entity_type="task",
            entity_id=task.id,
            description=f"Created task '{task.title}'",
        )
        if target_user_id != current_user.id:
            await create_notification(
                db,
                {
                    "user_id": target_user_id,
                    "title": "New task assigned",
                    "message": f"You have been assigned the task '{task.title}'.",
                    "notification_type": "TASK_ASSIGNED",
                    "channel": "DASHBOARD",
                },
                commit=False,
            )
        await db.commit()
        return task
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))


async def _assert_bulk_task_creator(db: AsyncSession, current_user: User) -> None:
    if current_user.role.name in [SUPER_ADMIN_ROLE_NAME, "Admin"]:
        return
    ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
    ud = (await db.execute(ud_stmt)).scalars().first()
    if not ud or str(ud.user_level).upper().replace(LEVEL_PREFIX, "") not in ["L0", "L1"]:
        raise HTTPException(
            status_code=403,
            detail="Only Managers or Admins can assign tasks to other users.",
        )


async def _create_bulk_task_item(
    item, payload: BulkTaskCreate, db: AsyncSession, current_user: User
):
    task_data = item.model_dump(exclude_unset=True)
    task_data["category"] = payload.category
    task_data["status"] = TaskStatus.DRAFT
    task_data["assigner_id"] = current_user.id
    owner_id = task_data.get("owner_id")
    if owner_id is None:
        owner_id = current_user.id
        task_data["owner_id"] = owner_id

    if str(owner_id) != str(current_user.id):
        await _validate_assigned_task_payload(
            db=db,
            current_user=current_user,
            assignee_id=UUID(str(owner_id)),
            category=payload.category,
            due_date=task_data.get("due_date"),
            grace_period_days=task_data.get("grace_period_days", 0),
        )
        if task_data.get("due_date") is None:
            raise HTTPException(
                status_code=400,
                detail="Assigned Task အတွက် Due Date မဖြစ်မနေရှိရမည်။",
            )
        task_data["grace_period_days"] = int(task_data.get("grace_period_days", 0) or 0)
    else:
        task_data["grace_period_days"] = int(task_data.get("grace_period_days", 0) or 0)
        task_data["due_date"] = task_data.get("due_date")

    if (
        "due_date" in task_data
        and task_data["due_date"] is not None
        and task_data.get("grace_period_days", 0) < 0
    ):
        raise HTTPException(
            status_code=400,
            detail="Allowed Late Days သည် 0 ထက်မနည်း ဖြစ်ရမည်။",
        )

    new_task = await task_repo.create_task(db, task_data)
    db.add(TaskHistory(
        task_id=new_task.id,
        changed_by=current_user.id,
        action="ASSIGNED",
        old_value="NONE",
        new_value=TaskStatus.DRAFT.value,
        remarks=f"[{payload.category}] Category ဖြင့် Bulk ဖန်တီးထားပါသည်",
    ))
    record_activity(
        db,
        actor_id=current_user.id,
        action="TASK_ASSIGNED",
        entity_type="task",
        entity_id=new_task.id,
        description=f"Assigned task '{new_task.title}'",
    )
    if owner_id != current_user.id:
        await create_notification(
            db,
            {
                "user_id": owner_id,
                "title": "New task assigned",
                "message": f"You have been assigned the task '{new_task.title}'.",
                "notification_type": "TASK_ASSIGNED",
                "channel": "DASHBOARD",
            },
            commit=False,
        )
    return new_task


@router.post('/bulk-create', response_model=list[TaskResponse], status_code=status.HTTP_201_CREATED, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def create_bulk_tasks(payload: BulkTaskCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    """ 🌟 Manager/Admin များမှ Task ကို Assign ချပေးသော Endpoint """
    enforce_permission(current_user, "tasks.create")
    try:
        await _assert_bulk_task_creator(db, current_user)

        created_tasks = []
        for item in payload.tasks:
            created_tasks.append(
                await _create_bulk_task_item(item, payload, db, current_user)
            )

        await db.commit()
        return created_tasks
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

async def _delete_task_evidence_files(task) -> None:
    if not task.evidence:
        return
    for evidence in task.evidence:
        try:
            if hasattr(storage_service, 'delete_file'):
                await storage_service.delete_file(evidence.file_path)
            elif hasattr(storage_service, 'remove_file'):
                await storage_service.remove_file(evidence.file_path)
        except Exception:  # noqa: S110 - evidence cleanup is best-effort during deletion
            pass


@router.delete('/{task_id}', status_code=status.HTTP_204_NO_CONTENT, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def delete_draft_task(task_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        task = await _authorized_task(db, task_id, current_user)
        if str(task.owner_id) != str(current_user.id):
            enforce_permission(current_user, "tasks.delete")
        else:
            enforce_permission(current_user, "tasks.delete", owner_id=task.owner_id)
            
        task_status = getattr(task.status, 'value', str(task.status))
        
        if task_status.upper() != "DRAFT":
            raise HTTPException(status_code=400, detail="DRAFT (ရေးဆွဲဆဲ) Task များကိုသာ ဖျက်ခွင့်ပြုပါသည်။")
            
        await _delete_task_evidence_files(task)

        await db.delete(task)
        await db.commit()
        return None
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

@router.get('/department-managers', tags=["Tasks"], responses={500: {"description": "Internal Server Error"}})
async def get_department_managers(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id, UserDepartment.is_deleted == False)
        current_user_dept = (await db.execute(ud_stmt)).scalars().first()

        department_id = current_user_dept.department_id if current_user_dept else None

        stmt = (
            select(User, UserDepartment)
            .join(UserDepartment, User.id == UserDepartment.user_id)
            .where(
                UserDepartment.department_id == department_id,
                UserDepartment.user_level.in_(['L0', 'L1', LEVEL_ZERO_ROLE_NAME, LEVEL_ONE_ROLE_NAME]),
                UserDepartment.is_deleted == False,
                User.id != current_user.id,
                User.is_active == True
            )
        )
        result = await db.execute(stmt)
        rows = result.all()

        manager_list = []
        for user_obj, ud_obj in rows:
            manager_list.append({
                "id": str(user_obj.id),
                "first_name": user_obj.first_name,
                "last_name": user_obj.last_name,
                "email": user_obj.email,
                "level": ud_obj.user_level
            })
        return manager_list
    except Exception:
        raise HTTPException(status_code=500, detail="Internal Server Error fetching managers.")


@router.get('/assignees', tags=["Tasks"])
async def get_task_assignees(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return only users this caller may assign a task to."""
    current_link = (
        await db.execute(
            select(UserDepartment).where(
                UserDepartment.user_id == current_user.id,
                UserDepartment.is_deleted == False,
            )
        )
    ).scalars().first()
    if not current_link:
        return []

    current_level = str(current_link.user_level).upper().replace(LEVEL_PREFIX, "")
    allowed_levels = {"L0": {"L1", "L2"}, "L1": {"L2"}}.get(current_level, set())
    if not allowed_levels:
        return []

    result = await db.execute(
        select(User, UserDepartment)
        .join(UserDepartment, User.id == UserDepartment.user_id)
        .where(
            UserDepartment.department_id == current_link.department_id,
            UserDepartment.user_level.in_(allowed_levels),
            UserDepartment.is_deleted == False,
            User.id != current_user.id,
            User.is_active == True,
            User.is_deleted == False,
        )
    )
    return [
        {
            "id": str(user.id),
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "level": link.user_level,
        }
        for user, link in result.all()
    ]


# 🌟 PERFORMANCE FIX: Add Pagination and DB-level filtering to prevent Memory Exhaustion
def _is_privileged_task_viewer(current_user: User) -> bool:
    return current_user.role.name in [
        SUPER_ADMIN_ROLE_NAME, "Admin", LEVEL_ZERO_ROLE_NAME
    ]


def _mask_l0_rejection_comments(tasks, is_l2: bool):
    my_tasks = []
    for task in tasks:
        task_dict = TaskResponse.model_validate(task)
        if is_l2:
            for review in task_dict.reviews:
                if (
                    review.reviewer_level in ["L0", LEVEL_ZERO_ROLE_NAME]
                    and review.action in ["REJECTED", "REJECTED_OVERRIDE"]
                ):
                    review.comments = (
                        "Returned from Management. Please wait for L1 supervisor instructions."
                    )
        my_tasks.append(task_dict)
    return my_tasks


@router.get('', response_model=list[TaskResponse], responses={500: {"description": "Internal Server Error"}})
async def get_my_tasks(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, gt=0, le=500),
    db: AsyncSession = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    enforce_permission(current_user, "tasks.view")
    try:
        # Base Query
        stmt = (
            select(Task)
            .options(
                selectinload(Task.reviews), 
                selectinload(Task.evidence),
                joinedload(Task.owner)
            )
            .where(
                Task.is_deleted == False,
                Task.category.is_not(None) 
            )
        )
        
        ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
        ud = (await db.execute(ud_stmt)).scalars().first()
        is_l2 = ud.user_level in ['L2', 'Level 2'] if ud else True
        is_privileged = _is_privileged_task_viewer(current_user)
        
        # 🌟 PERFORMANCE FIX: Database-Level Filtering instead of Python Loop Filtering
        if not is_privileged:
            stmt = stmt.where(
                or_(
                    Task.owner_id == current_user.id,
                    Task.assigner_id == current_user.id,
                    Task.id.in_(
                        select(TaskReview.task_id).where(
                            TaskReview.reviewer_id == current_user.id
                        )
                    ),
                )
            )

        # Pagination & Ordering
        stmt = stmt.order_by(Task.created_at.desc()).offset(skip).limit(limit)
        result = await db.execute(stmt)
        tasks = result.scalars().all()
        
        # Preserve the UI/UX logic for L2 users (Masking L0 rejection comments)
        if not is_privileged:
            return _mask_l0_rejection_comments(tasks, is_l2)
            
        return tasks
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database formatting error: {str(e)}")

@router.get('/batch/{batch_id}/details', response_model=list[TaskResponse], responses={404: {"description": "Not Found"}})
async def get_batch_tasks_details(batch_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "tasks.view")
    stmt = select(Task).options(
        selectinload(Task.reviews),
        selectinload(Task.evidence),
        joinedload(Task.owner)
    ).where(Task.batch_id == batch_id)
    
    result = await db.execute(stmt)
    tasks = result.scalars().unique().all()
    if not tasks:
        raise HTTPException(status_code=404, detail="Batch not found.")
    if current_user.role and current_user.role.name in GLOBAL_SCOPE_ROLES:
        return tasks
    if any(task.owner_id == current_user.id or task.assigner_id == current_user.id for task in tasks):
        return tasks
    queue = await db.execute(
        select(ApprovalQueue.id).where(
            ApprovalQueue.batch_id == batch_id,
            ApprovalQueue.reviewer_id == current_user.id,
        )
    )
    if queue.scalar_one_or_none() is None:
        raise HTTPException(status_code=404, detail="Batch not found.")
    return tasks

@router.post('/submit-batch', response_model=BatchSubmitResponse, responses={400: {"description": "Bad Request"}, 500: {"description": "Internal Server Error"}})
async def submit_task_batch(payload: BatchSubmitRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, "tasks.submit")
    try:
        batch = await workflow_repo.submit_task_batch(db, payload.task_ids, current_user.id, payload.reviewer_id)
        stmt = select(Task).where(Task.id.in_(payload.task_ids))
        tasks_to_update = (await db.execute(stmt)).scalars().all()

        current_time = datetime.now(UTC)

        for t in tasks_to_update:
            t.batch_id = batch.id
            t.assigner_id = payload.reviewer_id
            t.status = TaskStatus.PENDING_L1
            
            # 🌟 CORE FIX: Grace Period & Late Penalty Logic
            # due_date ရှိသော Assigned Task များအတွက်သာ အလုပ်လုပ်မည်
            if getattr(t, 'due_date', None):
                t_due = t.due_date if t.due_date.tzinfo else t.due_date.replace(tzinfo=UTC)
                diff = current_time - t_due
                days_late = diff.days
                
                grace_allowed = getattr(t, 'grace_period_days', 0)
                if days_late > grace_allowed:
                    t.is_late = True
                    t.late_days = days_late - grace_allowed
                else:
                    t.is_late = False
                    t.late_days = 0

            db.add(TaskHistory(
                task_id=t.id, changed_by=current_user.id, action="SUBMITTED_BATCH", 
                old_value=TaskStatus.DRAFT.value, new_value=TaskStatus.PENDING_L1.value, 
                remarks=f"Batch ဖြင့် တင်ပြလိုက်ပါသည် (Late: {t.is_late})"
            ))
            record_activity(
                db,
                actor_id=current_user.id,
                action="TASK_SUBMITTED",
                entity_type="task",
                entity_id=t.id,
                description=f"Submitted task '{t.title}' for review",
            )
            await create_notification(
                db,
                {
                    "user_id": payload.reviewer_id,
                    "title": "Task submitted for review",
                    "message": f"Task '{t.title}' is waiting for your review.",
                    "notification_type": "TASK_SUBMITTED",
                    "channel": "DASHBOARD",
                },
                commit=False,
            )
            
        await db.commit()
        return BatchSubmitResponse(
            batch_id=batch.id, total_tasks=batch.total_tasks, status=batch.status,
            message=f"Tasks ({batch.total_tasks}) ခုကို အောင်မြင်စွာ တင်ပြပြီးပါပြီ။"
        )
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# 🌟 PERFORMANCE FIX: Eliminate N+1 DB Queries from the Loop
@router.get('/approval-queue', response_model=list[QueueItemResponse])
async def get_approval_queue(db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    enforce_permission(current_user, TASK_APPROVE_PERMISSION)
    queues = await workflow_repo.get_my_approval_queue(db, current_user.id)
    result = []
    
    for q in queues:
        full_identity = "Unknown Submitter"
        submitter_id = None
        submitter_name = None
        submitter_avatar_url = None
        submitter_initials = "?"
        submitter_color_index = 0
        
        # 🌟 PERFORMANCE FIX: Because 'workflow_repo.get_my_approval_queue' already uses 
        # joinedload(ApprovalQueue.batch).joinedload(SubmissionBatch.submitter), 
        # the user object is already in memory. NO database queries needed here!
        if q.batch and q.batch.submitter:
            owner = q.batch.submitter
            submitter_id = owner.id
            submitter_name = f"{owner.first_name} {owner.last_name or ''}".strip() or owner.email
            submitter_avatar_url = avatar_url(owner.avatar_path, owner.avatar_updated_at)
            submitter_initials = avatar_initials(submitter_name, owner.email)
            submitter_color_index = avatar_color_index(owner.id)
            if owner.first_name:
                full_identity = f"{owner.first_name} {owner.last_name or ''} ({owner.email})".strip()
            else:
                full_identity = owner.email

        result.append(QueueItemResponse(
            queue_id=q.id, batch_id=q.batch_id, submitter_email=full_identity,
            submitter_id=submitter_id, submitter_name=submitter_name,
            avatar_url=submitter_avatar_url, initials=submitter_initials,
            avatar_color_index=submitter_color_index,
            total_tasks=q.batch.total_tasks, pending_count=q.pending_count, submitted_at=q.batch.created_at
        ))
    return result

def _validate_batch_review_payload(user_level, payload: BatchReviewRequest) -> None:
    if not user_level:
        raise HTTPException(status_code=403, detail='သင်၏ အကောင့်တွင် Level သတ်မှတ်ထားခြင်း မရှိပါ။')
    if payload.action in ["DELAY", "REJECTED"] and not payload.comments:
        raise HTTPException(status_code=400, detail='ပယ်ချပါက အကြောင်းပြချက် (Comments) မဖြစ်မနေ ထည့်သွင်းပေးရပါမည်။')
    if payload.action == "REJECTED" and not payload.deduction_rule:
        raise HTTPException(status_code=400, detail='Reject လုပ်မည်ဆိုပါက KPI ဖြတ်မည့် Deduction Rule အား မဖြစ်မနေ ရွေးချယ်ပေးရပါမည်။')


async def _batch_task_kpi_target(
    db: AsyncSession,
    task: Task,
    current_user: User,
    payload: BatchReviewRequest,
    user_level,
):
    target_kpi_user_id = None
    target_kpi_level = None
    target_kpi_dept = None
    owner_dept_id = None
    if task.owner and task.owner.user_departments:
        owner_dept_id = task.owner.user_departments[0].department_id

    if payload.action == "REJECTED":
        if user_level in ["L1", LEVEL_ONE_ROLE_NAME]:
            target_kpi_user_id = task.owner_id
            target_kpi_level = "L2"
            target_kpi_dept = owner_dept_id
        elif user_level in ["L0", LEVEL_ZERO_ROLE_NAME]:
            l1_review_stmt = select(TaskReview).where(
                TaskReview.task_id == task.id,
                TaskReview.reviewer_level.in_(["L1", LEVEL_ONE_ROLE_NAME]),
                TaskReview.action == "APPROVED",
            ).order_by(TaskReview.created_at.desc())
            l1_review = (await db.execute(l1_review_stmt)).scalars().first()
            if l1_review:
                target_kpi_user_id = l1_review.reviewer_id
                target_kpi_level = "L1"
                l1_ud_stmt = select(UserDepartment).where(
                    UserDepartment.user_id == l1_review.reviewer_id
                )
                l1_ud = (await db.execute(l1_ud_stmt)).scalars().first()
                target_kpi_dept = l1_ud.department_id if l1_ud else owner_dept_id
                db.add(TaskHistory(
                    task_id=task.id,
                    changed_by=current_user.id,
                    action="L1_PENALIZED",
                    old_value="APPROVED",
                    new_value="REJECTED",
                    remarks=(
                        "L1 Supervisor penalized for incorrect approval: "
                        f"{payload.deduction_rule}"
                    ),
                ))
    elif payload.action == "APPROVED":
        target_kpi_user_id = task.owner_id
        target_kpi_level = "L2"
        target_kpi_dept = owner_dept_id
    return target_kpi_user_id, target_kpi_level, target_kpi_dept


async def _process_batch_review_task(
    db: AsyncSession,
    task: Task,
    current_user: User,
    payload: BatchReviewRequest,
    user_level,
    recalculated_dates: set,
) -> None:
    if payload.action == "APPROVED" and payload.next_reviewer_id:
        task.assigner_id = payload.next_reviewer_id
    target_date = task.due_date if task.due_date else datetime.now(app_timezone)
    target_kpi_user_id, target_kpi_level, target_kpi_dept = (
        await _batch_task_kpi_target(
            db, task, current_user, payload, user_level
        )
    )
    if not (target_kpi_user_id and target_kpi_dept):
        return
    unique_key = f"{target_kpi_user_id}_{target_date.strftime('%Y-%m-%d')}"
    if unique_key in recalculated_dates:
        return
    await trigger_kpi_recalculation(
        db=db,
        user_level=target_kpi_level,
        user_id=target_kpi_user_id,
        department_id=target_kpi_dept,
        target_date=target_date,
    )
    recalculated_dates.add(unique_key)


@router.post('/batch/{batch_id}/review', response_model=BatchSubmitResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 500: {"description": "Internal Server Error"}})
async def review_task_batch_api(batch_id: UUID, payload: BatchReviewRequest, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    if payload.action == "APPROVED":
        enforce_permission(current_user, TASK_APPROVE_PERMISSION)
    elif payload.action == "REJECTED":
        enforce_permission(current_user, "tasks.reject")
    elif payload.action == "DELAY":
        enforce_permission(current_user, "tasks.edit")
    try:
        ud_stmt = select(UserDepartment).where(UserDepartment.user_id == current_user.id)
        ud = (await db.execute(ud_stmt)).scalars().first()
        user_level = ud.user_level if ud else None
        _validate_batch_review_payload(user_level, payload)

        batch = await workflow_repo.review_task_batch(
            db=db, batch_id=batch_id, reviewer_id=current_user.id, reviewer_level=user_level, 
            action=payload.action, comments=payload.comments, next_reviewer_id=payload.next_reviewer_id,
            deduction_rule=payload.deduction_rule
        )
        
        stmt = select(Task).options(
            selectinload(Task.owner).selectinload(User.user_departments)
        ).where(Task.batch_id == batch_id)
        
        batch_tasks = (await db.execute(stmt)).scalars().all()
        recalculated_dates = set()
        for task in batch_tasks:
            await _process_batch_review_task(
                db, task, current_user, payload, user_level, recalculated_dates
            )
                
        await db.commit()
        return BatchSubmitResponse(batch_id=batch.id, total_tasks=batch.total_tasks, status=batch.status, message=f"Batch အတွင်းရှိ Task များအားလုံးကို {payload.action} ပြုလုပ်ပြီးပါပြီ။")
    except ValueError as e:
        await db.rollback()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Server Validation Error: {str(e)}")

@router.post('/{task_id}/resign', response_model=TaskResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}})
async def resign_task_to_l2(task_id: UUID, payload: TaskReviewInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = await _authorize_workflow_actor(
        db, task_id, current_user, required_level="L1", permission="tasks.return"
    )
    if task.status != TaskStatus.PENDING_L0:
        raise HTTPException(status_code=400, detail="Task is not awaiting L1 resignation.")
    latest_l1_review = max(
        (
            review for review in (task.reviews or [])
            if review.reviewer_level in {"L1", LEVEL_ONE_ROLE_NAME}
            and review.action == "APPROVED"
        ),
        key=lambda review: review.created_at,
        default=None,
    )
    if not latest_l1_review or latest_l1_review.reviewer_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the assigned L1 reviewer can resign this task.")
    try:
        ud = (await db.execute(select(UserDepartment).where(UserDepartment.user_id == current_user.id))).scalars().first()
        user_level = ud.user_level if ud else "L1"
        old_status = task.status.value
        task.status = TaskStatus.REJECTED
        
        new_review = TaskReview(task_id=task.id, reviewer_id=current_user.id, reviewer_level=user_level, action="RESIGNED", comments=payload.comments)
        db.add(new_review)
        db.add(TaskHistory(task_id=task.id, changed_by=current_user.id, action="RESIGNED", old_value=old_status, new_value=task.status.value, remarks="L1 မှ L2 သို့ ပြန်လည်ပြင်ဆင်ရန် ညွှန်ကြားလိုက်ပါသည်"))
        
        await db.commit()
        await db.refresh(task)
        return task
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=str(e))

# 🌟 CRITICAL FIX: Anti-Malware and DoS Protection applied
@router.post("/{task_id}/evidence", response_model=TaskEvidenceResponse, responses={404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 415: {"description": "Unsupported Media Type"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Service Unavailable"}})
async def upload_task_evidence(task_id: UUID, file: UploadFile = File(...), db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        task = await _authorized_task(db, task_id, current_user)
        if task.status not in {TaskStatus.DRAFT, TaskStatus.REJECTED}:
            raise HTTPException(status_code=409, detail="Evidence cannot be added in the current task state.")

        # 1. Validation: Prevent Denial of Service (DoS) via huge file memory loads
        file.file.seek(0, 2)
        file_size = file.file.tell()
        await file.seek(0)
        if file_size > MAX_FILE_SIZE:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="File too large. Maximum size allowed is 10MB.")

        # 2. Validation: Prevent Malware Uploads (MIME Type Check 🌟)
        content = await file.read()

        # 🌟 File ရဲ့ Extension အစစ်ကို ဖတ်ခြင်း
        mime_type = _detect_mime_type(content, file.filename)

        if mime_type not in ALLOWED_MIME_TYPES:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Security Violation: File content does not match its extension.")

        # 3. Extension Validation
        ext = os.path.splitext(file.filename)[1].lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Unsupported file extension. Upload blocked for security reasons.")

        file_hash = hashlib.sha256(content).hexdigest()
        await file.seek(0)
        
        object_name = await storage_service.upload_file(
            file,
            folder=f"task_evidence/task_{task_id}",
            content_type=mime_type,
        )
        evidence_data = {
            "task_id": task.id,
            "file_name": file.filename,
            "file_path": object_name, 
            "file_hash": file_hash,
            "uploaded_by": current_user.id
        }
        
        new_evidence = TaskEvidence(**evidence_data)
        db.add(new_evidence)
        record_activity(
            db,
            actor_id=current_user.id,
            action="EVIDENCE_UPLOADED",
            entity_type="task",
            entity_id=task.id,
            description=f"Uploaded evidence for task '{task.title}'",
        )
        await db.commit()
        await db.refresh(new_evidence)
        
        return new_evidence
    except HTTPException:
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to upload evidence: {str(e)}")

@router.get("/evidence/view", summary="Get Presigned URL to View Evidence", responses={404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Storage backend unavailable"}})
async def get_evidence_url(
    evidence_id: UUID,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    try:
        evidence = (await db.execute(
            select(TaskEvidence).where(TaskEvidence.id == evidence_id)
        )).scalars().first()
        if not evidence:
            raise HTTPException(status_code=404, detail="Evidence file not found.")
        await _authorized_task(db, evidence.task_id, current_user)
        presigned_url = storage_service.get_presigned_url(object_name=evidence.file_path, expiration=3600)
        return {"view_url": presigned_url, "expires_in_seconds": 3600}
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(status_code=500, detail="Could not generate file access link")

@router.delete("/evidence/{evidence_id}", status_code=status.HTTP_200_OK, tags=["Tasks"], responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 500: {"description": "Internal Server Error"}, 503: {"description": "Service Unavailable"}})
async def delete_task_evidence(evidence_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    try:
        stmt = select(TaskEvidence).where(TaskEvidence.id == evidence_id).with_for_update()
        evidence = (await db.execute(stmt)).scalars().first()
        
        if not evidence:
            raise HTTPException(status_code=404, detail="Evidence file not found.")
        task = (
            await db.execute(
                select(Task).where(Task.id == evidence.task_id).with_for_update()
            )
        ).scalars().first()
        if not task:
            raise HTTPException(status_code=404, detail=ERR_TASK_NOT_FOUND)

        role_name = current_user.role.name if current_user.role else ""
        is_admin = role_name in {SUPER_ADMIN_ROLE_NAME, "HR", "Admin"}
        is_owner_or_uploader = (
            evidence.uploaded_by == current_user.id
            or task.owner_id == current_user.id
        )
        is_editable = task.status in {TaskStatus.DRAFT, TaskStatus.REJECTED}
        if not is_admin and not (is_owner_or_uploader and is_editable):
            raise HTTPException(
                status_code=403,
                detail="Only the evidence uploader or task owner may delete evidence while the task is editable.",
            )

        delete_storage_file = getattr(storage_service, "delete_file", None)
        if not callable(delete_storage_file):
            delete_storage_file = getattr(storage_service, "remove_file", None)
        if not callable(delete_storage_file):
            raise HTTPException(
                status_code=503,
                detail="Evidence storage cleanup is unavailable.",
            )

        task_id = task.id
        object_name = evidence.file_path
        db.add(
            TaskHistory(
                task_id=task_id,
                changed_by=current_user.id,
                action="EVIDENCE_DELETED",
                old_value="PRESENT",
                new_value="DELETED",
                remarks=f"Evidence '{evidence.file_name}' removed.",
            )
        )
        record_activity(
            db,
            actor_id=current_user.id,
            action="TASK_EVIDENCE_DELETED",
            entity_type="task",
            entity_id=task_id,
            description=f"Deleted evidence '{evidence.file_name}' from task '{task.title}'",
        )
        await db.delete(evidence)
        await db.commit()
        try:
            await delete_storage_file(object_name)
        except Exception as storage_error:
            logger.exception(
                "Evidence database record deleted but object cleanup failed; "
                "orphan cleanup required for task %s",
                task_id,
            )
            raise HTTPException(
                status_code=500,
                detail="Evidence record was removed, but storage cleanup failed; contact an administrator.",
            ) from storage_error
        return {"message": "Evidence file removed successfully."}
    except HTTPException:
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("Failed to remove task evidence %s", evidence_id)
        raise HTTPException(status_code=500, detail="Failed to remove evidence.") from exc

# =====================================================================
# 🌟 FIX 2: V2 Resubmit လလုပ်ရာတွင် Auto Submit မလုပ်ရန်နှင့် Old Task မဖျက်ရန်
# =====================================================================
@router.post('/{task_id}/resubmit', response_model=TaskResponse, responses={403: {"description": "Forbidden"}, 404: {"description": "Not Found"}, 409: {"description": "Conflict"}, 500: {"description": "Internal Server Error"}})
async def resubmit_task(task_id: UUID, payload: TaskCreate, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    old_task = await _authorized_task(db, task_id, current_user)
    if old_task.owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the task owner can resubmit this task.")
    if old_task.status != TaskStatus.REJECTED:
        raise HTTPException(status_code=409, detail="Only rejected tasks can be resubmitted.")
        
    try:
        task_data = payload.model_dump(exclude_unset=True)
        task_data['owner_id'] = old_task.owner_id
        
        current_version = old_task.version if old_task.version else 1
        existing_resubmission_stmt = select(Task.id).where(
            Task.parent_task_id == old_task.id,
            Task.version == current_version + 1,
            Task.is_deleted == False,
        )
        existing_resubmission_id = (
            await db.execute(existing_resubmission_stmt)
        ).scalars().first()
        if existing_resubmission_id is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A resubmission for this task version already exists.",
            )

        task_data['version'] = current_version + 1
        task_data['parent_task_id'] = old_task.id
        
        # 🌟 V2 အသစ်ကို DRAFT အနေဖြင့်သာ အရင်ဖန်တီးမည် (Evidence တင်ရန်အတွက်)
        task_data['status'] = TaskStatus.DRAFT

        # နောက်ဆုံး Review လုပ်ခဲ့သော Manager ဆီသို့သာ ပြန်လည်ရောက်ရှိစေရန်
        l1_review_stmt = select(TaskReview).where(
            TaskReview.task_id == old_task.id, 
            TaskReview.reviewer_level.in_(['L1', LEVEL_ONE_ROLE_NAME])
        ).order_by(TaskReview.created_at.desc()) # နောက်ဆုံး Review ကို ယူမည်
        
        l1_review = (await db.execute(l1_review_stmt)).scalars().first()
        correct_assigner_id = l1_review.reviewer_id if l1_review else old_task.assigner_id
        task_data['assigner_id'] = correct_assigner_id

        new_task = await task_repo.create_task(db, task_data)
        
        # 🌟 Old Evidence ကို ကူးမထည့်တော့ပါ (V2 အတွက် ဖိုင်အသစ် ပြန်တင်ရပါမည်)
        # 🌟 Old Task ကို is_deleted = True လုပ်ခြင်း လုံးဝ ဖယ်ရှားလိုက်ပါသည် (သမိုင်းကြောင်း ကျန်ရှိစေရန်)
        # 🌟 Auto Submit Batch လုပ်ခြင်းကို လုံးဝ ဖယ်ရှားလိုက်ပါသည် (Frontend မှ ခေါ်မည်)
        
        db.add(TaskHistory(
            task_id=new_task.id, changed_by=current_user.id, action="RESUBMITTED", 
            old_value="REJECTED", new_value=TaskStatus.DRAFT.value, 
            remarks="မူကွဲအသစ်ဖြင့် ပြန်လည်တင်ပြရန် အသင့်ဖြစ်နေပါပြီ။"
        ))
        record_activity(
            db,
            actor_id=current_user.id,
            action="TASK_RESUBMITTED",
            entity_type="task",
            entity_id=new_task.id,
            description=f"Resubmitted task '{new_task.title}'",
        )
        
        await db.commit()
        
        # 🌟 FIX: MissingGreenlet error ကို ရှင်းလင်းရန် db.refresh() ဖယ်ရှားပြီး Eager Load ဖြင့် ပြန်လည်ဆွဲထုတ်ခြင်း
        stmt = select(Task).options(
            selectinload(Task.reviews),
            selectinload(Task.evidence),
            selectinload(Task.owner)
        ).where(Task.id == new_task.id)
        
        refreshed_task = (await db.execute(stmt)).scalars().first()
        
        return refreshed_task
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to process V2 resubmission: {str(e)}")

# 🌟 CRITICAL FIX: Data Stealing (Insecure Direct Object Reference) Prevention applied
@router.get('/{task_id}', response_model=TaskResponse, responses={404: {"description": "Not Found"}})
async def get_single_task(task_id: UUID, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    return await _authorized_task(
        db,
        task_id,
        current_user,
        allow_workflow_scope=True,
    )

@router.post('/{task_id}/review/l1', response_model=TaskResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def l1_review_task(task_id: UUID, payload: TaskReviewInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = await _authorize_workflow_actor(db, task_id, current_user, required_level="L1")
    if task.status != TaskStatus.PENDING_L1:
        raise HTTPException(status_code=400, detail="Task is not awaiting L1 review.")
    
    old_status = getattr(task.status, 'value', str(task.status))
    updated_task = await task_service.review_task_l1(db, task_id, payload, current_user.id)
    
    db.add(TaskHistory(task_id=task.id, changed_by=current_user.id, action=payload.action, old_value=old_status, new_value=updated_task.status.value, remarks=payload.comments))
    await db.commit()
    return updated_task

@router.post('/{task_id}/verify/l0', response_model=TaskResponse, responses={400: {"description": "Bad Request"}, 403: {"description": "Forbidden"}, 404: {"description": "Not Found"}})
async def l0_verify_task(task_id: UUID, payload: TaskReviewInput, db: AsyncSession = Depends(get_db), current_user: User = Depends(get_current_user)):
    task = await _authorize_workflow_actor(db, task_id, current_user, required_level="L0")
    if task.status != TaskStatus.PENDING_L0:
        raise HTTPException(status_code=400, detail="Task is not awaiting L0 verification.")
    
    old_status = getattr(task.status, 'value', str(task.status))
    updated_task = await task_service.verify_task_l0(db, task_id, payload, current_user.id)
    
    action_str = "VERIFIED" if payload.action == "APPROVED" else "REJECTED_OVERRIDE"
    db.add(TaskHistory(task_id=task.id, changed_by=current_user.id, action=action_str, old_value=old_status, new_value=updated_task.status.value, remarks=payload.comments))
    await db.commit()
    return updated_task