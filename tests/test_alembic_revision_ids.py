"""Regression guard for the 2026-10-28 deploy blocker.

Root cause: ``alembic_version.version_num`` was ``VARCHAR(32)`` while
the revision id ``20261026_kpi_record_exceeded_categories`` is 39
characters, so stamping it failed and (because ``env.py`` runs all
migrations in one transaction) the whole upgrade rolled back and the
app restart-looped. The fix widened the column to ``VARCHAR(255)``
in ``20261028_widen_alembic_ver`` and chained it BEFORE the long id.

These tests fail in CI before any such state can reach production:

1. No revision id may exceed the widened column width (255).
2. The chain must have exactly one head (a second head means an
   unmerged chain, which also breaks ``upgrade head``).
3. Any revision id longer than the ORIGINAL width (32) must have
   the widen migration as an ancestor, so the column is already
   widened when its stamp runs.

Pure Alembic file reading -- no app import, no database needed.
"""

from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS_DIR = REPO_ROOT / "migrations"

ORIGINAL_WIDTH = 32
WIDENED_WIDTH = 255
WIDEN_REVISION = "20261028_widen_alembic_ver"


def _script_directory() -> ScriptDirectory:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return ScriptDirectory.from_config(config)


def _ancestors(script: ScriptDirectory, revision_id: str) -> set:
    """All ancestor revision ids of ``revision_id`` (handles merges)."""
    seen = set()
    stack = [revision_id]
    while stack:
        current = stack.pop()
        revision = script.get_revision(current)
        if revision is None:
            continue
        down = revision.down_revision
        parents = down if isinstance(down, tuple) else ((down,) if down else ())
        for parent in parents:
            if parent not in seen:
                seen.add(parent)
                stack.append(parent)
    return seen


def test_all_revision_ids_fit_widened_column():
    script = _script_directory()
    too_long = [
        (rev.revision, len(rev.revision))
        for rev in script.walk_revisions()
        if len(rev.revision) > WIDENED_WIDTH
    ]
    assert not too_long, (
        "Revision ids exceed alembic_version.version_num "
        f"VARCHAR({WIDENED_WIDTH}) and would fail stamping on deploy: {too_long}"
    )


def test_single_alembic_head():
    script = _script_directory()
    heads = script.get_heads()
    assert len(heads) == 1, (
        f"Expected exactly one Alembic head, found {len(heads)}: {heads}. "
        "Re-chain the extra head before merging."
    )


def test_widen_migration_precedes_long_revision_ids():
    script = _script_directory()
    long_ids = [
        rev.revision
        for rev in script.walk_revisions()
        if len(rev.revision) > ORIGINAL_WIDTH
    ]
    assert long_ids, "Expected at least one >32-char revision id in the chain"
    uncovered = [
        revision_id
        for revision_id in long_ids
        if WIDEN_REVISION not in _ancestors(script, revision_id)
    ]
    assert not uncovered, (
        f"These >{ORIGINAL_WIDTH}-char revision ids stamp BEFORE the widen "
        f"migration {WIDEN_REVISION} and would fail on a VARCHAR(32) "
        f"alembic_version column: {uncovered}"
    )
