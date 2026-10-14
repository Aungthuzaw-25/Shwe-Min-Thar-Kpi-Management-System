import unicodedata
from datetime import UTC, datetime
from uuid import UUID

import regex


def avatar_initials(display_name: str | None, email: str | None = None) -> str:
    name = unicodedata.normalize("NFC", display_name or "").strip()
    words = name.split()
    if not words:
        email_name = unicodedata.normalize("NFC", email or "").strip()
        words = [email_name] if email_name else []
    if not words:
        return "?"
    selected_words = [words[0], *([words[-1]] if len(words) > 1 else [])]
    return "".join(
        cluster[0]
        for cluster in (regex.findall(r"\X", word) for word in selected_words)
        if cluster
    ).upper()


def avatar_color_index(user_id: UUID | str) -> int:
    return UUID(str(user_id)).int % 8


def avatar_url(avatar_path: str | None, updated_at: datetime | None) -> str | None:
    if not avatar_path:
        return None
    if updated_at is None:
        version = 0
    else:
        timestamp = updated_at.replace(tzinfo=UTC) if updated_at.tzinfo is None else updated_at
        version = int(timestamp.timestamp())
    return f"/api/v1/account/avatars/{avatar_path}?v={version}"
