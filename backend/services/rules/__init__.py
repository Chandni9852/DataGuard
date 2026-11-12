"""Importing this package registers every rule implementation."""
from backend.services.rules import completeness, consistency, referential, uniqueness, validity  # noqa: F401
from backend.services.rules.base import REGISTRY, Rule, RuleContext  # noqa: F401
