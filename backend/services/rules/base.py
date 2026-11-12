"""Rule framework.

A rule is a small class that looks at the whole batch (a DataFrame of raw text values) and
returns a boolean mask of failing rows. Rules never touch the database directly: anything
they need from outside the batch (reference data, today's date) comes through RuleContext.
That keeps them pure and unit-testable.

Which rules run, their severity and their parameters live in the `validation_rules` table.
The registry below maps a rule_code to the class that implements it. Adding a rule means
writing one class, registering its code, and inserting one row.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, ClassVar

import pandas as pd

from backend.errors import DataGuardError

TRANSACTION_COLUMNS = ["transaction_id", "customer_id", "transaction_amount",
                       "transaction_date", "merchant", "transaction_status"]

# (table, column) pairs a referential rule may point at. Rule parameters come from the
# database, so table and column names are whitelisted instead of being trusted.
ALLOWED_REFERENCES = {("customers", "customer_id")}


@dataclass
class RuleContext:
    today: date
    # Reference sets keyed "table.column". Either pre-filled (tests) or loaded on demand.
    references: dict[str, set[str]] = field(default_factory=dict)
    reference_loader: Callable[[str, str], set[str]] | None = None
    cache: dict = field(default_factory=dict)

    def reference(self, table: str, column: str) -> set[str]:
        key = f"{table}.{column}"
        if key not in self.references:
            if self.reference_loader is None:
                raise DataGuardError("reference_missing", f"Reference data {key} is not available.")
            self.references[key] = self.reference_loader(table, column)
        return self.references[key]


def text(s: pd.Series) -> pd.Series:
    """Column as plain strings with NULL as ''."""
    return s.where(s.notna(), "").astype(str)


def is_missing(s: pd.Series) -> pd.Series:
    """True where the cell is NULL, empty or whitespace-only."""
    return text(s).str.strip() == ""


REGISTRY: dict[str, type["Rule"]] = {}


def register(*codes: str):
    def deco(cls):
        for code in codes:
            if code in REGISTRY:
                raise RuntimeError(f"Rule code {code} registered twice")
            REGISTRY[code] = cls
        return cls
    return deco


class Rule:
    """Base class. Subclasses set `category` and implement `evaluate` and `describe`."""

    category: ClassVar[str]
    message: ClassVar[str] = "Validation failed"

    def __init__(self, code: str, params: dict | None = None, severity: str = "MEDIUM"):
        self.code = code
        self.severity = severity
        self.params = self.build_params(code, params or {})

    # -- configuration --------------------------------------------------------
    @classmethod
    def defaults(cls, code: str) -> dict:
        return {}

    @classmethod
    def check_params(cls, code: str, params: dict) -> None:
        """Raise DataGuardError('rule_config') if params are unusable."""

    @classmethod
    def build_params(cls, code: str, params: dict) -> dict:
        merged = {**cls.defaults(code), **params}
        cls.check_params(code, merged)
        return merged

    @staticmethod
    def bad_config(code: str, why: str) -> DataGuardError:
        return DataGuardError("rule_config", f"Rule {code} is misconfigured: {why}", rule_code=code)

    # -- behaviour --------------------------------------------------------------
    @property
    def column(self) -> str | None:
        return self.params.get("column")

    def preflight(self, ctx: RuleContext) -> None:
        """Raise before any state changes if the rule cannot run (e.g. reference data missing)."""

    def evaluate(self, df: pd.DataFrame, ctx: RuleContext) -> pd.Series:
        raise NotImplementedError

    def describe(self, value) -> str:
        return self.message


def require_known_column(code: str, params: dict) -> None:
    col = params.get("column")
    if col not in TRANSACTION_COLUMNS:
        raise Rule.bad_config(code, f"'column' must be one of {TRANSACTION_COLUMNS}, got {col!r}")
