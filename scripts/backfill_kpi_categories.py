"""Backfill task categories from existing KPI formula JSON payloads."""

import asyncio

from sqlalchemy import func, select

from app.database.connection import AsyncSessionLocal
from app.modules.kpi.models import KPIFormula, KpiCategory


async def backfill_categories() -> int:
    async with AsyncSessionLocal() as db:
        formulas = (await db.execute(select(KPIFormula))).scalars().all()
        names = {
            category.get("name", "").strip()
            for formula in formulas
            if isinstance(formula.categories, list)
            for category in formula.categories
            if isinstance(category, dict)
            and category.get("name")
            and category.get("name", "").strip()
        }
        existing = await db.execute(
            select(KpiCategory.name).where(
                func.lower(KpiCategory.name).in_(
                    {name.casefold() for name in names}
                )
            )
        )
        existing_names = {
            name.casefold() for name in existing.scalars().all() if name
        }
        new_categories = [
            KpiCategory(name=name)
            for name in sorted(names)
            if name.casefold() not in existing_names
        ]
        db.add_all(new_categories)
        await db.commit()
        return len(new_categories)


if __name__ == "__main__":
    count = asyncio.run(backfill_categories())
    print(f"Inserted {count} KPI categories.")
