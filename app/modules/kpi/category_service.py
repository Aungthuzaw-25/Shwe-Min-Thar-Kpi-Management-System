import json
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.modules.departments.models import UserDepartment
from app.modules.kpi.models import KPIFormula, KpiCategory
from app.modules.users.models import Position, User


def _normalized_level(value) -> str:
    return str(value or "").strip().upper().replace("LEVEL ", "")


def _normalized_name(value) -> str:
    return str(value or "").strip().casefold()


def _formula_categories(formula: KPIFormula) -> set[str]:
    raw_categories = formula.categories
    if isinstance(raw_categories, str):
        try:
            raw_categories = json.loads(raw_categories)
        except json.JSONDecodeError:
            return set()
    if not isinstance(raw_categories, list):
        return set()

    names = set()
    for category in raw_categories:
        name = category.get("name") if isinstance(category, dict) else getattr(category, "name", None)
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


async def get_eligible_kpi_categories_for_user(
    db: AsyncSession, user_id: UUID
) -> set[str]:
    """Return canonical KPI categories configured for a user's current context."""
    user = await db.get(User, user_id)
    if not user or not user.is_active or not user.position_id:
        return set()

    department_link = (
        await db.execute(
            select(UserDepartment)
            .where(UserDepartment.user_id == user_id)
            .order_by(UserDepartment.department_id)
        )
    ).scalars().first()
    if not department_link:
        return set()

    position = await db.get(Position, user.position_id)
    position_name = _normalized_name(position.name if position else "")
    formulas = (
        await db.execute(
            select(KPIFormula).where(
                KPIFormula.department_id == department_link.department_id,
                KPIFormula.user_level == _normalized_level(department_link.user_level),
                KPIFormula.is_active.is_(True),
            )
        )
    ).scalars().all()

    matching_formulas = []
    fallback_formulas = []
    for formula in formulas:
        formula_position_name = _normalized_name(formula.position_name)
        position_matches = (
            formula.position_id == user.position_id
            or (formula_position_name and formula_position_name == position_name)
        )
        if position_matches:
            matching_formulas.append(formula)
        elif not formula.position_id and not formula_position_name:
            fallback_formulas.append(formula)

    selected_formulas = matching_formulas or fallback_formulas
    formula_names = {
        name
        for formula in selected_formulas
        for name in _formula_categories(formula)
    }
    if not formula_names:
        return set()

    canonical_categories = (
        await db.execute(
            select(KpiCategory.name).where(KpiCategory.name.in_(formula_names))
        )
    ).scalars().all()
    canonical_by_key = {
        _normalized_name(name): name for name in canonical_categories
    }
    return {
        canonical_by_key[_normalized_name(name)]
        for name in formula_names
        if _normalized_name(name) in canonical_by_key
    }
