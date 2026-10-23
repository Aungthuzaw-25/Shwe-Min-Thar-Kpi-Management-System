"""Full data export (the real counterpart of the settings-only backup).

`GET /api/v1/system/backup` exports only the `system_settings` rows.
This module exports every core table to a ZIP of per-table JSON
files, built for a future restore/import step:

- one ``tables/<table>.json`` file per table (a JSON array of rows),
  so memory stays bounded: each table is read in chunks of
  ``CHUNK_SIZE`` rows and streamed into the ZIP entry, never held
  whole in memory, and the ZIP itself is written to a temp file on
  disk, not to RAM;
- a ``manifest.json`` with row counts and exactly what was withheld.

Credential material NEVER leaves in an export:

- whole tables excluded: ``auth_sessions`` (live refresh-token
  hashes), ``password_history`` (old password hashes) and
  ``token_blacklist`` (revoked-token hashes) — ephemeral security
  state, useless for a restore, dangerous in a file;
- ``users.hashed_password`` is dropped (accounts restore with a
  password reset, never with exported hashes);
- ``system_settings`` values pass through the settings backup's
  sanitizer, which redacts secret-looking fields (email provider
  keys etc.) exactly like the settings-only backup does.

``task_evidence.file_hash`` is kept on purpose: it is an integrity
checksum of the evidence file, not a credential.
"""

from __future__ import annotations

import base64
import json
import zipfile
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timezone import utc_now_naive
from app.database.connection import Base

CHUNK_SIZE = 500

EXCLUDED_TABLES: dict[str, str] = {
    "auth_sessions": "live refresh-token hashes (credential material)",
    "password_history": "previous password hashes (credential material)",
    "token_blacklist": "revoked token hashes (credential material)",
}

EXCLUDED_COLUMNS: dict[str, set[str]] = {
    "users": {"hashed_password"},
}


def exportable_tables() -> list[tuple[str, Any, list[str]]]:
    """(table name, table object, exported column names), name-sorted."""
    spec = []
    for name in sorted(Base.metadata.tables):
        if name in EXCLUDED_TABLES:
            continue
        table = Base.metadata.tables[name]
        dropped = EXCLUDED_COLUMNS.get(name, set())
        columns = [c.name for c in table.columns if c.name not in dropped]
        spec.append((name, table, columns))
    return spec


def _json_safe(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, dict):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(v) for v in value]
    return value


async def _write_table(
    db: AsyncSession,
    zf: zipfile.ZipFile,
    name: str,
    table: Any,
    columns: list[str],
    settings_sanitizer: Callable[[str, Any], Any] | None,
) -> int:
    """Stream one table into its ZIP entry in chunks; return row count."""
    cols = [table.c[col] for col in columns]
    count = 0
    offset = 0
    with zf.open(f"tables/{name}.json", "w", force_zip64=True) as fh:
        fh.write(b"[")
        while True:
            stmt = (
                select(*cols)
                .order_by(table.c.id)
                .offset(offset)
                .limit(CHUNK_SIZE)
            )
            rows = (await db.execute(stmt)).mappings().all()
            if not rows:
                break
            for row in rows:
                record = {col: _json_safe(row[col]) for col in columns}
                if name == "system_settings" and settings_sanitizer is not None:
                    record["value"] = _json_safe(
                        settings_sanitizer(record.get("key", ""), record.get("value"))
                    )
                payload = json.dumps(record, ensure_ascii=False)
                fh.write(b"\n" if count == 0 else b",\n")
                fh.write(payload.encode("utf-8"))
                count += 1
            offset += len(rows)
            if len(rows) < CHUNK_SIZE:
                break
        fh.write(b"\n]\n" if count else b"]\n")
    return count


async def build_full_export_zip(
    db: AsyncSession,
    zip_path: str | Path,
    *,
    settings_sanitizer: Callable[[str, Any], Any] | None = None,
) -> dict[str, Any]:
    """Write the full-export ZIP to ``zip_path``; return the manifest."""
    counts: dict[str, int] = {}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, table, columns in exportable_tables():
            counts[name] = await _write_table(
                db, zf, name, table, columns, settings_sanitizer
            )
        manifest = {
            "type": "Full Data Export",
            "generated_at": utc_now_naive().isoformat(),
            "tables": counts,
            "total_rows": sum(counts.values()),
            "excluded_tables": EXCLUDED_TABLES,
            "excluded_columns": {
                table: sorted(cols)
                for table, cols in EXCLUDED_COLUMNS.items()
            },
            "notes": [
                "Credential material is never exported: auth_sessions, "
                "password_history and token_blacklist are excluded as "
                "whole tables and users.hashed_password is dropped.",
                "system_settings values are redacted with the same "
                "sanitizer as the settings backup.",
                "Files stored in object storage (evidence, avatars, "
                "logos) are NOT in this export; only database rows are.",
                "There is currently no import/restore endpoint for "
                "this file.",
            ],
        }
        zf.writestr(
            "manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False)
        )
    return manifest
