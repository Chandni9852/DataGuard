from backend.errors import DataGuardError
from backend.services.rules.base import ALLOWED_REFERENCES, Rule, is_missing, register, text


@register("R01")
class CustomerExists(Rule):
    """customer_id must exist in the reference table (orphan check)."""
    category = "REFERENTIAL_INTEGRITY"

    @classmethod
    def defaults(cls, code):
        return {"column": "customer_id", "reference_table": "customers", "reference_column": "customer_id"}

    @classmethod
    def check_params(cls, code, params):
        pair = (params.get("reference_table"), params.get("reference_column"))
        if pair not in ALLOWED_REFERENCES:
            raise Rule.bad_config(code, f"reference {pair} is not an allowed reference table/column")

    def preflight(self, ctx):
        # An empty reference table would flag every record as an orphan, which is
        # misleading. Refuse to run instead.
        if not ctx.reference(self.params["reference_table"], self.params["reference_column"]):
            raise DataGuardError(
                "reference_missing",
                f"Reference table '{self.params['reference_table']}' is empty. "
                "Load the customers dataset before validating transactions.")

    def evaluate(self, df, ctx):
        known = ctx.reference(self.params["reference_table"], self.params["reference_column"])
        col = df[self.column]
        return ~is_missing(col) & ~text(col).isin(known)

    def describe(self, value):
        return f"customer_id '{value}' does not exist in the customers reference data"
