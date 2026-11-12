import pandas as pd

from backend.services.rules.base import Rule, register
from backend.services.rules.validity import decimal_params, parse_amounts, parse_dates


@register("K01")
class DateNotInFuture(Rule):
    """Valid dates later than the batch's ingestion date. Unparseable dates are V03's job."""
    category = "CONSISTENCY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_date"}

    def evaluate(self, df: pd.DataFrame, ctx) -> pd.Series:
        today = ctx.today
        return parse_dates(df, ctx).map(lambda d: d is not None and d > today).astype(bool)

    def describe(self, value):
        return f"transaction_date '{value}' is in the future"


@register("K02")
class AmountCeiling(Rule):
    category = "CONSISTENCY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_amount", "max": 1000000}

    @classmethod
    def check_params(cls, code, params):
        decimal_params(code, params, "max")

    def evaluate(self, df, ctx):
        ceiling = decimal_params(self.code, self.params, "max")
        return parse_amounts(df, ctx).map(lambda a: a is not None and a > ceiling).astype(bool)

    def describe(self, value):
        return f"transaction_amount '{value}' exceeds the maximum of {self.params['max']}"
