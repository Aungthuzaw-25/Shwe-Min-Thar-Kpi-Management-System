"""
File Validation, MIME Type Sniffing and SHA-256 Checksum Utilities
"""
import os
import hashlib
import magic
from fastapi import UploadFile, HTTPException, status
from typing import Set, List
from app.core.constants import ALLOWED_EVIDENCE_EXTENSIONS, ALLOWED_EVIDENCE_MIME_TYPES, MAX_EVIDENCE_FILE_SIZE

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
    ext = os.path.splitext(file.filename).lower()
    if ext not in allowed_extensions:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Unsupported file extension '{ext}'. Allowed: {', '.join(allowed_extensions)}"
        )

    content = await file.read()
    await file.seek(0)

    # 3. Binary Signature MIME Sniffing Check
    mime_type = magic.from_buffer(content, mime=True)
    if not any(mime in mime_type.lower() for mime in allowed_mimes):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"Security Alert: File signature ({mime_type}) does not match a valid document."
        )

    file_hash = calculate_content_hash(content)
    return content, file_hash