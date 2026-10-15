import io
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

import pytest
from fastapi import HTTPException, UploadFile

from app.utils import file_handler


def upload(name: str, content: bytes) -> UploadFile:
    return UploadFile(filename=name, file=io.BytesIO(content))


@pytest.mark.asyncio
async def test_valid_pdf_is_accepted(monkeypatch):
    monkeypatch.setattr(file_handler, "_detect_mime_type", lambda content, filename: "application/pdf")
    content, digest = await file_handler.validate_and_read_upload(
        upload("evidence.pdf", b"%PDF synthetic")
    )
    assert content.startswith(b"%PDF")
    assert len(digest) == 64


@pytest.mark.asyncio
async def test_renamed_executable_is_rejected(monkeypatch):
    monkeypatch.setattr(file_handler, "_detect_mime_type", lambda content, filename: "application/x-dosexec")
    with pytest.raises(HTTPException) as exc:
        await file_handler.validate_and_read_upload(upload("evidence.pdf", b"MZ executable"))
    assert exc.value.status_code == 415


@pytest.mark.asyncio
async def test_mismatched_extension_and_mime_are_rejected(monkeypatch):
    monkeypatch.setattr(file_handler, "_detect_mime_type", lambda content, filename: "image/png")
    with pytest.raises(HTTPException) as exc:
        await file_handler.validate_and_read_upload(upload("evidence.pdf", b"not a pdf"))
    assert exc.value.status_code == 415


@pytest.mark.asyncio
async def test_unknown_mime_is_rejected(monkeypatch):
    monkeypatch.setattr(file_handler, "_detect_mime_type", lambda content, filename: "application/octet-stream")
    with pytest.raises(HTTPException) as exc:
        await file_handler.validate_and_read_upload(upload("evidence.pdf", b"unknown"))
    assert exc.value.status_code == 415


@pytest.mark.asyncio
async def test_production_fails_closed_without_magic(monkeypatch):
    monkeypatch.setenv("ENV", "production")
    monkeypatch.setattr(file_handler, "magic", None)
    with pytest.raises(HTTPException) as exc:
        file_handler._detect_mime_type(b"%PDF", "evidence.pdf")
    assert exc.value.status_code == 503


def test_task_upload_mime_detection_fails_closed_without_magic(monkeypatch):
    from app.modules.tasks import router as tasks_router

    monkeypatch.setattr(tasks_router, "magic", None)
    with pytest.raises(HTTPException) as exc_info:
        tasks_router._detect_mime_type(b"%PDF-1.7", "evidence.pdf")
    assert exc_info.value.status_code == 503


def test_task_upload_mime_detection_rejects_extension_mismatch(monkeypatch):
    from app.modules.tasks import router as tasks_router

    monkeypatch.setattr(
        tasks_router,
        "magic",
        SimpleNamespace(from_buffer=lambda content, mime: "application/pdf"),
    )
    with pytest.raises(HTTPException) as exc_info:
        tasks_router._detect_mime_type(b"%PDF-1.7", "evidence.jpg")
    assert exc_info.value.status_code == 415


def test_task_upload_mime_detection_recognizes_office_archive_structure(monkeypatch):
    from app.modules.tasks import router as tasks_router

    archive_bytes = io.BytesIO()
    with ZipFile(archive_bytes, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("xl/workbook.xml", "<workbook/>")
    monkeypatch.setattr(
        tasks_router,
        "magic",
        SimpleNamespace(from_buffer=lambda content, mime: "application/zip"),
    )

    detected = tasks_router._detect_mime_type(archive_bytes.getvalue(), "report.xlsx")

    assert detected == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
