"""Validity rules and the parsing helpers shared with the consistency rules."""
from datetime import datetime
from decimal import Decimal, InvalidOperation

import pandas as pd

from backend.services.rules.base import Rule, RuleContext, is_missing, register, text

_NUMBER = r"^-?\d+(\.\d+)?$"
_DATE_SHAPE = r"^\d{4}-\d{2}-\d{2}$"
DATE_FORMAT = "%Y-%m-%d"


def decimal_params(code: str, params: dict, key: str) -> Decimal:
    try:
        return Decimal(str(params[key]))
    except (KeyError, InvalidOperation):
        raise Rule.bad_config(code, f"'{key}' must be a number, got {params.get(key)!r}")


def parse_amounts(df: pd.DataFrame, ctx: RuleContext) -> pd.Series:
    """Series of Decimal for parseable amounts, None elsewhere. Cached per batch."""
    if "amounts" not in ctx.cache:
        st = text(df["transaction_amount"]).str.strip()
        ok = st.str.match(_NUMBER)
        out = pd.Series([None] * len(df), index=df.index, dtype=object)
        out[ok] = st[ok].map(Decimal)
        ctx.cache["amounts"] = out
    return ctx.cache["amounts"]


def parse_dates(df: pd.DataFrame, ctx: RuleContext) -> pd.Series:
    """Series of datetime.date for valid YYYY-MM-DD values, None elsewhere. Cached per batch.

    Uses the standard library rather than pandas Timestamps so valid dates outside the
    nanosecond range (year < 1677 or > 2262) are not wrongly rejected.
    """
    if "dates" not in ctx.cache:
        st = text(df["transaction_date"]).str.strip()
        shaped = st.str.match(_DATE_SHAPE)
        out = pd.Series([None] * len(df), index=df.index, dtype=object)

        def parse(v):
            try:
                return datetime.strptime(v, DATE_FORMAT).date()
            except ValueError:
                return None
        out[shaped] = st[shaped].map(parse)
        ctx.cache["dates"] = out
    return ctx.cache["dates"]


@register("V01")
class AmountIsNumeric(Rule):
    category = "VALIDITY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_amount"}

    def evaluate(self, df, ctx):
        return ~is_missing(df[self.column]) & parse_amounts(df, ctx).isna()

    def describe(self, value):
        return f"transaction_amount '{value}' is not a valid number"


@register("V02")
class AmountPositive(Rule):
    category = "VALIDITY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_amount", "min_exclusive": 0}

    @classmethod
    def check_params(cls, code, params):
        decimal_params(code, params, "min_exclusive")

    def evaluate(self, df, ctx):
        bound = decimal_params(self.code, self.params, "min_exclusive")
        amounts = parse_amounts(df, ctx)
        return amounts.map(lambda a: a is not None and a <= bound).astype(bool)

    def describe(self, value):
        return f"transaction_amount '{value}' must be greater than {self.params['min_exclusive']}"


@register("V03")
class DateFormat(Rule):
    category = "VALIDITY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_date", "format": DATE_FORMAT}

    @classmethod
    def check_params(cls, code, params):
        if params.get("format") != DATE_FORMAT:
            raise Rule.bad_config(code, f"only the format {DATE_FORMAT} (YYYY-MM-DD) is supported")

    def evaluate(self, df, ctx):
        return ~is_missing(df[self.column]) & parse_dates(df, ctx).isna()

    def describe(self, value):
        return f"transaction_date '{value}' is not a valid date in YYYY-MM-DD format"


@register("V04")
class AllowedStatus(Rule):
    """Case-sensitive match against the allowed list (surrounding whitespace is ignored)."""
    category = "VALIDITY"

    @classmethod
    def defaults(cls, code):
        return {"column": "transaction_status", "allowed": ["COMPLETED", "PENDING", "FAILED", "REVERSED"]}

    @classmethod
    def check_params(cls, code, params):
        allowed = params.get("allowed")
        if not isinstance(allowed, list) or not allowed or not all(isinstance(a, str) for a in allowed):
            raise Rule.bad_config(code, "'allowed' must be a non-empty list of strings")

    def evaluate(self, df, ctx):
        col = df[self.column]
        return ~is_missing(col) & ~text(col).str.strip().isin(self.params["allowed"])

    def describe(self, value):
        return f"transaction_status '{value}' is not one of {', '.join(self.params['allowed'])}"
