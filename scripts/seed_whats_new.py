"""Seed the what's-new launch entries (one run per environment).

Usage: python scripts/seed_whats_new.py
Idempotent by title — re-running inserts nothing new and
never resurrects an entry an SA soft-deleted. Entries are
normal published rows (not migration data); later entries
are created through the publish UI instead.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.database.connection import AsyncSessionLocal
from app.modules.system.whats_new_seed import seed_whats_new


async def main() -> None:
    async with AsyncSessionLocal() as db:
        inserted, skipped = await seed_whats_new(db)
    print(f"whats-new seeds: {inserted} inserted, {skipped} skipped")


if __name__ == "__main__":
    asyncio.run(main())
