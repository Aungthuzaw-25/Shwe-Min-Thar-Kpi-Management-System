import uuid

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID

from app.core.timezone import utc_now_naive
from app.database.connection import Base


class IdempotencyKey(Base):
    """One row per (user, Idempotency-Key) submit intent.

    Written by app.core.idempotency: a row starts as ``in_progress``
    before the handler runs and becomes ``completed`` with the stored
    response once the handler succeeds, so a retried submit returns
    the original result instead of executing twice. Rows expire
    (``expires_at``) and are then treated as never seen.
    """

    __tablename__ = "idempotency_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )
    key = Column(String(128), nullable=False)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False)
    request_hash = Column(String(64), nullable=False)
    state = Column(String(16), nullable=False, default="in_progress")
    response_status = Column(Integer, nullable=True)
    response_body = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now_naive, nullable=False)
    expires_at = Column(DateTime, nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "key", name="uq_idempotency_user_key"),
        Index("idx_idempotency_keys_expires_at", "expires_at"),
    )
