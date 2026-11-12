import pandas as pd

from backend.services.rules.base import Rule, is_missing, register, require_known_column

COLUMN_BY_CODE = {
    "C01": "transaction_id", "C02": "customer_id", "C03": "transaction_amount",
    "C04": "transaction_date", "C05": "transaction_status", "C06": "merchant",
}


@register(*COLUMN_BY_CODE)
class CompletenessRule(Rule):
    """Mandatory field is NULL, empty or whitespace-only."""
    category = "COMPLETENESS"

    @classmethod
    def defaults(cls, code):
        return {"column": COLUMN_BY_CODE.get(code)}

    @classmethod
    def check_params(cls, code, params):
        require_known_column(code, params)

    def evaluate(self, df: pd.DataFrame, ctx) -> pd.Series:
        return is_missing(df[self.column])

    def describe(self, value) -> str:
        return f"{self.column} is missing or blank"
