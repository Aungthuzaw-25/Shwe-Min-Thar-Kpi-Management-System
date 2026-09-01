"""
File Validation, MIME Type Sniffing and SHA-256 Checksum Utilities
"""
import os
import hashlib
import mimetypes
from fastapi import UploadFile, HTTPException, status
from typing import Set, List

try:
    import magic
except ImportError:  # pragma: no cover - libmagic may not be installed.
    magic = None

from app.core.constants import ALLOWED_EVIDENCE_EXTENSIONS, ALLOWED_EVIDENCE_MIME_TYPES, MAX_EVIDENCE_FILE_SIZE

def _detect_mime_type(file_bytes: bytes, filename: str | None = None) -> str:
    """Fallback MIME detection when libmagic is unavailable."""
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

    if file_bytes.startswith(b"%PDF"):
        return "application/pdf"
    if file_bytes.startswith(b"\x89PNG"):
        return "image/png"
    if file_bytes.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if file_bytes.startswith(b"PK"):
        return "application/zip"
    return "application/octet-stream"


def calculate_content_hash(content: bytes) -> str:
    """Calculate SHA-256 hash for file tamper verification"""
    return hashlib.sha256(content).hexdigest()

async def validate_and_read_upload(
    file: UploadFile,
    max_size: int = MAX_EVIDENCE_FILE_SIZE,
    allowed_extensions: Set[str] = ALLOWED_EVIDENCE_EXTENSIONS,
    allowed_mimes: List[str] = ALLOWED_EVIDENCE_MIME_TYPES
) -> tuple[bytes, str]:
    """
    Validates file size (DoS prevention), file extension, and binary signature (magic MIME check).
    Returns (file_bytes, sha256_hash).
    """
    if not file or not file.filename:
        raise HTTPException(status_code=400, detail="No file provided.")

    # 1. Size Check
    file.file.seek(0, 2)
    file_size = file.file.tell()
    await file.seek(0)
    
    if file_size > max_size:
        max_mb = max_size / (1024 * 1024)
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, 
            detail=f"File too large. Maximum size allowed is {max_mb:.0f}MB."
        )

    # 2. Extension Check
    ext = os.path.splitext(file.filename)[1].lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(allowed_extensions)}"
        )

    content = await file.read()
    await file.seek(0)

    # 3. Binary Signature MIME Sniffing Check
    mime_type = _detect_mime_type(content, file.filename)
    if not any(mime in mime_type.lower() for mime in allowed_mimes):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Security Alert: File signature ({mime_type}) does not match a valid document."
        )

    file_hash = calculate_content_hash(content)
    return content, file_hash