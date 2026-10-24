"""Submit idempotency (Idempotency-Key header).

End-to-end against a probe app wired exactly like production (guard
dependency + completion middleware + replay handler), with the
idempotency store on in-memory SQLite. The probe handler counts its
own executions, so "did the write run twice?" is directly visible.
"""

import uuid
from datetime import timedelta

import pytest
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core import idempotency
from app.core.dependencies import get_current_user
from app.core.idempotency import (
    REPLAYED_HEADER,
    IdempotentReplay,
    IdempotencyCompletionMiddleware,
    idempotency_guard,
)
from app.core.timezone import utc_now_naive
from app.models.idempotency import IdempotencyKey

USER_A = uuid.uuid4()
USER_B = uuid.uuid4()


@pytest.fixture()
def probe(monkeypatch, tmp_path):
    db_file = tmp_path / "idem.db"
    sync_engine = create_engine(f"sqlite:///{db_file}")
    IdempotencyKey.__table__.create(sync_engine, checkfirst=True)
    sync_engine.dispose()
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_file}")
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(idempotency, "AsyncSessionLocal", factory)

    calls = {"count": 0}
    app = FastAPI()

    async def fake_user(request: Request):
        from types import SimpleNamespace

        return SimpleNamespace(
            id=uuid.UUID(request.headers.get("x-user-id", str(USER_A)))
        )

    app.dependency_overrides[get_current_user] = fake_user

    def replay_handler(request: Request, exc: IdempotentReplay):
        return JSONResponse(
            status_code=exc.status_code,
            content=exc.body,
            headers={REPLAYED_HEADER: "true"},
        )

    app.add_exception_handler(IdempotentReplay, replay_handler)
    app.add_middleware(IdempotencyCompletionMiddleware)

    @app.post(
        "/things",
        status_code=201,
        dependencies=[Depends(idempotency_guard)],
    )
    async def create_thing(payload: dict):
        calls["count"] += 1
        return {"created": calls["count"], "echo": payload}

    @app.post("/fragile", dependencies=[Depends(idempotency_guard)])
    async def fragile(payload: dict):
        calls["count"] += 1
        if calls["count"] == 1:
            raise HTTPException(status_code=500, detail="boom")
        return {"created": calls["count"]}

    client = TestClient(app, raise_server_exceptions=False)

    def seed_row(**kwargs):
        sync_engine = create_engine(f"sqlite:///{db_file}")
        with sync_engine.begin() as conn:
            conn.execute(insert(IdempotencyKey.__table__).values(**kwargs))
        sync_engine.dispose()

    return client, calls, seed_row


def _headers(user=USER_A, key="key-1"):
    headers = {"x-user-id": str(user)}
    if key is not None:
        headers["Idempotency-Key"] = key
    return headers


def test_duplicate_submit_returns_first_result_without_reexecuting(probe):
    client, calls, _seed = probe
    first = client.post(
        "/things", json={"name": "task"}, headers=_headers()
    )
    assert first.status_code == 201
    assert REPLAYED_HEADER not in first.headers

    second = client.post(
        "/things", json={"name": "task"}, headers=_headers()
    )
    assert second.status_code == 201
    assert second.json() == first.json()  # the original result verbatim
    assert second.headers[REPLAYED_HEADER] == "true"
    assert calls["count"] == 1  # the write ran exactly once


def test_different_key_executes_normally(probe):
    client, calls, _seed = probe
    client.post("/things", json={"n": 1}, headers=_headers(key="k1"))
    again = client.post(
        "/things", json={"n": 1}, headers=_headers(key="k2")
    )
    assert again.status_code == 201
    assert REPLAYED_HEADER not in again.headers
    assert calls["count"] == 2


def test_same_key_different_body_is_422(probe):
    client, calls, _seed = probe
    client.post("/things", json={"n": 1}, headers=_headers())
    conflict = client.post(
        "/things", json={"n": 2}, headers=_headers()
    )
    assert conflict.status_code == 422
    assert calls["count"] == 1


def test_keys_are_scoped_per_user(probe):
    client, calls, _seed = probe
    client.post("/things", json={"n": 1}, headers=_headers(user=USER_A))
    other = client.post(
        "/things", json={"n": 1}, headers=_headers(user=USER_B)
    )
    assert other.status_code == 201
    assert REPLAYED_HEADER not in other.headers
    assert calls["count"] == 2  # user B's key is a different key


def test_no_header_means_no_dedupe(probe):
    client, calls, _seed = probe
    client.post("/things", json={"n": 1}, headers=_headers(key=None))
    client.post("/things", json={"n": 1}, headers=_headers(key=None))
    assert calls["count"] == 2


def _seed_values(*, user_id, key, state, created_delta, expires_delta, request_hash="x"):
    return {
        "id": uuid.uuid4(),
        "user_id": user_id,
        "key": key,
        "method": "POST",
        "path": "/things",
        "request_hash": request_hash,
        "state": state,
        "response_status": 201 if state == "completed" else None,
        "response_body": '{"created": 99}' if state == "completed" else None,
        "created_at": utc_now_naive() + created_delta,
        "expires_at": utc_now_naive() + expires_delta,
    }


def test_expired_key_counts_as_new(probe):
    client, calls, seed_row = probe
    seed_row(**_seed_values(
        user_id=USER_A,
        key="old-key",
        state="completed",
        created_delta=timedelta(hours=-25),
        expires_delta=timedelta(hours=-1),
        request_hash="0" * 64,
    ))
    response = client.post(
        "/things", json={"n": 1}, headers=_headers(key="old-key")
    )
    assert response.status_code == 201
    assert REPLAYED_HEADER not in response.headers
    assert calls["count"] == 1


def test_fresh_in_progress_key_is_409(probe):
    client, calls, seed_row = probe
    # Seed with the hash the real request will produce (httpx sends
    # compact JSON, no spaces).
    body = b'{"n":1}'
    request = type(
        "R", (), {"method": "POST", "url": type("U", (), {"path": "/things"})()}
    )()
    digest = idempotency._request_hash(request, body)
    seed_row(**_seed_values(
        user_id=USER_A,
        key="busy-key",
        state="in_progress",
        created_delta=timedelta(seconds=0),
        expires_delta=timedelta(hours=24),
        request_hash=digest,
    ))
    response = client.post(
        "/things", json={"n": 1}, headers=_headers(key="busy-key")
    )
    assert response.status_code == 409
    assert calls["count"] == 0


def test_failed_request_releases_the_key(probe):
    client, calls, _seed = probe
    first = client.post(
        "/fragile", json={}, headers=_headers(key="fragile-key")
    )
    assert first.status_code == 500
    retry = client.post(
        "/fragile", json={}, headers=_headers(key="fragile-key")
    )
    assert retry.status_code == 200
    assert retry.json() == {"created": 2}
    # And the successful retry is now the replayable result.
    replay = client.post(
        "/fragile", json={}, headers=_headers(key="fragile-key")
    )
    assert replay.headers[REPLAYED_HEADER] == "true"
    assert calls["count"] == 2


def test_model_has_user_key_unique_constraint():
    constraints = {
        c.name
        for c in IdempotencyKey.__table__.constraints
        if c.__class__.__name__ == "UniqueConstraint"
    }
    assert "uq_idempotency_user_key" in constraints
    columns = {c.name for c in IdempotencyKey.__table__.columns}
    assert {
        "user_id",
        "key",
        "request_hash",
        "state",
        "response_status",
        "response_body",
        "expires_at",
    } <= columns
