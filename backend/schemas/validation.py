import uuid
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.quality import QualityOut


class RuleOut(BaseModel):
    rule_id: int
    rule_code: str
    rule_name: str
    rule_category: str
    rule_description: str
    severity: str
    parameters: dict
    is_active: bool
    implemented: bool


class RuleUpdate(BaseModel):
    severity: Literal["CRITICAL", "HIGH", "MEDIUM", "LOW"] | None = None
    is_active: bool | None = None
    parameters: dict | None = None
    updated_by: str | None = Field(None, description="Recorded in the audit log")


class RuleFailureCount(BaseModel):
    rule_code: str
    rule_name: str
    category: str
    severity: str
    failures: int


class ValidationSummary(BaseModel):
    batch_id: uuid.UUID
    dataset_name: str
    processing_status: str
    total_records: int
    invalid_records: int
    valid_records: int | None          # None until validation has completed
    total_failures: int
    failures_by_rule: list[RuleFailureCount]
    failures_by_category: dict[str, int]
    failures_by_severity: dict[str, int]
    quality: QualityOut | None = None
    duration_ms: int | None
    rules_executed: list[dict] | None


class ValidateRequest(BaseModel):
    requested_by: str | None = None
