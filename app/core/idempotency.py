"""Submit idempotency: a retried write must not execute twice.

Contract (agreed with Core/Frontend, 2026-10-23):

- The client sends ``Idempotency-Key: <uuid>`` on a submit. One key
  stands for one submit *intent*: retries of the same intent reuse
  the same key; a new intent gets a new key. No header → the request
  behaves exactly as before (the layer is opt-in per request).
- First request: an ``in_progress`` row is written for
  (user, key) *before* the handler runs, in its own transaction, so
  a handler rollback cannot erase it and a concurrent duplicate can
  see it. On a successful (< 400) JSON response the row becomes
  ``completed`` with the status + body stored.
- Duplicate (same user, same key, same request body): the stored
  response is returned verbatim, with an ``Idempotency-Replayed:
  true`` header. The handler does NOT run again.
- Same key, different body → 422. Still ``in_progress`` → 409 (the
  client retries later); an ``in_progress`` row older than
  ``IN_PROGRESS_STALE_MINUTES`` is treated as abandoned (crashed
  request) and taken over.
- Failures are never replayed: a >= 500 response deletes the marker
  so a later retry may execute; 4xx responses are not stored either
  (re-executing a deterministic validation/state failure reproduces
  the same answer).
- Keys expire after ``IDEMPOTENCY_TTL_HOURS`` (default 24h); an
  expired key is treated as never seen. Keys are scoped per user —
  another user's key never collides.

Storage is the ``idempotency_keys`` DB table on purpose: the dedupe
record must be as durable as the data it protects (this app's Redis
is a fail-open cache and could lose it).
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from fastapi import Depends, HTTPException, Request, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import settings
from app.core.dependencies import get_current_user
from app.core.timezone import utc_now_naive
from app.database.connection import AsyncSessionLocal
from app.models.idempotency import IdempotencyKey
from app.modules.users.models import User

logger = logging.getLogger(__name__)

HEADER_NAME = "Idempotency-Key"
REPLAYED_HEADER = "Idempotency-Replayed"
MAX_KEY_LENGTH = 128
MAX_STORED_BODY_BYTES = 64 * 1024
IN_PROGRESS_STALE_MINUTES = 5


class IdempotentReplay(Exception):
    """Raised by the guard to short-circuit with the stored response."""

    def __init__(self, status_code: int, body: dict | list):
        super().__init__("idempotent replay")
        self.status_code = status_code
        self.body = body


def _request_hash(request: Request, body: bytes) -> str:
    digest = hashlib.sha256()
    digest.update(request.method.encode())
    digest.update(b"\n")
    digest.update(request.url.path.encode())
    digest.update(b"\n")
    digest.update(body)
    return digest.hexdigest()


def _validate_key(raw: str | None) -> str | None:
    if raw is None:
        return None
    key = raw.strip()
    if not key or len(key) > MAX_KEY_LENGTH:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid Idempotency-Key header.",
        )
    return key


async def _find_row(
    db: AsyncSession, user_id, key: str
) -> IdempotencyKey | None:
    return (
        await db.execute(
            select(IdempotencyKey).where(
                IdempotencyKey.user_id == user_id,
                IdempotencyKey.key == key,
            )
        )
    ).scalars().first()


def _mismatch() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        detail="Idempotency-Key was already used with a different request.",
    )


async def idempotency_guard(
    request: Request,
    current_user: User = Depends(get_current_user),
) -> None:
    """Route dependency: claim the key, or replay/stop a duplicate.

    Runs before the handler. On a claim it stashes the row id on
    ``request.state`` for IdempotencyCompletionMiddleware, which
    records the outcome after the handler responds.
    """
    key = _validate_key(request.headers.get(HEADER_NAME))
    if key is None:
        return
    body = await request.body()
    request_hash = _request_hash(request, body)
    now = utc_now_naive()

    async with AsyncSessionLocal() as db:
        row = await _find_row(db, current_user.id, key)
        if row is not None and row.expires_at <= now:
            await db.delete(row)
            await db.commit()
            row = None
        if row is not None:
            if row.request_hash != request_hash:
                raise _mismatch()
            if row.state == "completed":
                raise IdempotentReplay(
                    row.response_status,
                    json.loads(row.response_body or "{}"),
                )
            stale_before = now - timedelta(
                minutes=IN_PROGRESS_STALE_MINUTES
            )
            if row.created_at > stale_before:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail=(
                        "A request with this Idempotency-Key is still "
                        "being processed."
                    ),
                )
            # Abandoned marker (crashed request): take it over.
            await db.delete(row)
            await db.commit()
            row = None

        claim = IdempotencyKey(
            user_id=current_user.id,
            key=key,
            method=request.method,
            path=request.url.path,
            request_hash=request_hash,
            state="in_progress",
            created_at=now,
            expires_at=now
            + timedelta(hours=settings.IDEMPOTENCY_TTL_HOURS),
        )
        db.add(claim)
        try:
            await db.flush()
            claim_id = claim.id
            await db.commit()
        except IntegrityError:
            # A concurrent request claimed the same key first.
            await db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "A request with this Idempotency-Key is still "
                    "being processed."
                ),
            )
        request.state.idempotency_claim_id = claim_id


async def _complete_claim(claim_id, response_status: int, body: bytes) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(IdempotencyKey, claim_id)
        if row is None:
            return
        row.state = "completed"
        row.response_status = response_status
        row.response_body = body.decode("utf-8", errors="replace")
        await db.commit()


async def _release_claim(claim_id) -> None:
    async with AsyncSessionLocal() as db:
        row = await db.get(IdempotencyKey, claim_id)
        if row is not None:
            await db.delete(row)
            await db.commit()


class IdempotencyCompletionMiddleware(BaseHTTPMiddleware):
    """Record the outcome of a claimed idempotent request.

    Successful JSON responses are stored for replay; failures release
    the claim so a retry may execute. Never breaks the response: all
    store errors are logged and swallowed.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        claim_id = getattr(request.state, "idempotency_claim_id", None)
        if claim_id is None:
            return response
        try:
            if response.status_code >= 500:
                await _release_claim(claim_id)
                return response
            content_type = response.headers.get("content-type", "")
            if "application/json" not in content_type:
                await _release_claim(claim_id)
                return response
            chunks = []
            total = 0
            async for chunk in response.body_iterator:
                chunks.append(chunk)
                total += len(chunk)
            body = b"".join(chunks)
            if total <= MAX_STORED_BODY_BYTES and response.status_code < 400:
                await _complete_claim(claim_id, response.status_code, body)
            else:
                await _release_claim(claim_id)
            return Response(
                content=body,
                status_code=response.status_code,
                headers={
                    name: value
                    for name, value in response.headers.items()
                    if name.lower() not in ("content-length", "content-type")
                },
                media_type="application/json",
            )
        except Exception:
            logger.exception(
                "Failed to record idempotency outcome; response unaffected"
            )
            return response
