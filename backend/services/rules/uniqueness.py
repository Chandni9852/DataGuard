import pandas as pd

from backend.services.rules.base import TRANSACTION_COLUMNS, Rule, is_missing, register


@register("U01")
class DuplicateTransactionId(Rule):
    """Second and later occurrences of a transaction_id within the batch.

    The first occurrence is kept as the legitimate record, so N copies produce N-1 failures.
    Missing IDs are not compared here (completeness already reports them).
    """
    category = "UNIQUENESS"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_id"}

    def evaluate(self, df: pd.DataFrame, ctx) -> pd.Series:
        col = df[self.column]
        return col.duplicated(keep="first") & ~is_missing(col)

    def describe(self, value) -> str:
        return f"Duplicate transaction_id '{value}': it already appears earlier in this batch"


@register("U02")
class DuplicateRecord(Rule):
    """Row identical to an earlier row across all business columns."""
    category = "UNIQUENESS"

    @property
    def column(self):
        return None

    def evaluate(self, df: pd.DataFrame, ctx) -> pd.Series:
        return df.duplicated(subset=TRANSACTION_COLUMNS, keep="first")

    def describe(self, value) -> str:
        return "Record is an exact duplicate of an earlier record in this batch"
