from fastapi import HTTPException, status
from app.modules.kpi.models import KPIFormula, FormulaStatus

class KPICalculationService:
    @staticmethod
    def validate_formula_weights(formula: KPIFormula):
        """
        Validates that the sum of category weights in a formula equals exactly 1.0 (100%).
        This must be called before a formula transitions out of DRAFT status.
        """
        # Allow DRAFT status to have incomplete weights while editing
        if formula.status == FormulaStatus.DRAFT:
            return

        total_weight = 0.0
        
        if isinstance(formula.categories, list):
            for category in formula.categories:
                weight = category.get("weight", 0)
                try:
                    total_weight += float(weight)
                except (ValueError, TypeError):
                    pass
        
        # 🌟 FIX: 100.0 အစား Pydantic Schema နှင့်အညီ 1.0 ဖြင့် ပြင်ဆင်စစ်ဆေးထားသည်
        if round(total_weight, 2) != 1.00:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Formula cannot leave DRAFT status. Total weight must equal exactly 1.0 (100%). Current total is {total_weight}."
            )