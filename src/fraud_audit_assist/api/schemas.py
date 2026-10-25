"""Request and response bodies. Kept apart from storage rows so the database can change without breaking clients."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from fraud_audit_assist.domain.cases import AnalystDecision, Confidence
from fraud_audit_assist.domain.events import AwareDatetime, Event
from fraud_audit_assist.services.ingestion import MAX_BATCH


class IngestRequest(BaseModel):
    events: list[Event] = Field(min_length=1, max_length=MAX_BATCH)


class IngestResponse(BaseModel):
    ingested: list[uuid.UUID]
    duplicates: list[uuid.UUID]
    rescored_accounts: list[str]
    opened_cases: list[uuid.UUID]


class RuleHitOut(BaseModel):
    rule_id: str
    points: int
    evidence_event_ids: list[uuid.UUID]
    triggered_at: datetime
    explanation: str


class AlertOut(BaseModel):
    alert_id: uuid.UUID
    account_id: str
    score: int
    band: str
    rules_version: str
    opened_at: datetime
    updated_at: datetime
    hits: list[RuleHitOut] | None = None


class FindingOut(BaseModel):
    """The Finding with Pseudonyms turned back into real identifiers, for the analyst only."""

    verdict: str
    confidence: Confidence
    summary: str
    recommendation: str
    evidence: dict[str, list[uuid.UUID]]


class DecisionOut(BaseModel):
    outcome: str
    action: str
    analyst_id: str
    notes: str
    decided_at: datetime


class InterventionOut(BaseModel):
    action: str
    until: datetime | None
    created_at: datetime


class CaseSummaryOut(BaseModel):
    case_id: uuid.UUID
    alert_id: uuid.UUID
    account_id: str
    status: str
    band: str
    score: int
    opened_at: datetime
    not_investigated: bool
    investigation_failed: bool
    review_flagged: bool


class CaseOut(CaseSummaryOut):
    finding: FindingOut | None
    rejection_reasons: list[str]
    failure: str | None
    prompt_versions: str | None
    usage: list[dict[str, Any]]
    trace_id: str | None
    decision: DecisionOut | None
    interventions: list[InterventionOut]


class DecisionIn(AnalystDecision):
    pass


class ConfirmedFraudIn(BaseModel):
    decided_at: AwareDatetime


class AuditEntryOut(BaseModel):
    kind: str
    actor: str
    model: str | None
    prompt_version: str | None
    rules_version: str | None
    evidence: dict[str, Any]
    input_tokens: int
    output_tokens: int
    cost: float
    details: dict[str, Any]
    created_at: datetime


class LinkedAccountOut(BaseModel):
    account_id: str
    link_event_id: uuid.UUID
    confirmed_fraud: bool


class LinkedIdentifierOut(BaseModel):
    kind: str
    label: str
    value: str
    link_event_id: uuid.UUID
    others: list[LinkedAccountOut]


class RulesOut(BaseModel):
    pack: str
    version: str
    rules: list[dict[str, Any]]
    bands: dict[str, int]
    risk_period_days: float
