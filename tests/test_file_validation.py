import io

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
