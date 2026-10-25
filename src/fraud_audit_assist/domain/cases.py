"""Cases, Findings and Analyst Decisions: the human-in-the-loop half of the domain."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class CaseStatus(StrEnum):
    OPEN = "Open"
    INVESTIGATING = "Investigating"
    AWAITING_APPROVAL = "AwaitingApproval"
    # Held only while a decision is being applied, so two analysts cannot decide one Case at once.
    DECIDING = "Deciding"
    CLOSED = "Closed"


class Confidence(StrEnum):
    LOW = "Low"
    MEDIUM = "Medium"
    HIGH = "High"


class DecisionOutcome(StrEnum):
    CONFIRMED_FRAUD = "ConfirmedFraud"
    FALSE_POSITIVE = "FalsePositive"


class Finding(BaseModel):
    """The Investigator's written conclusion. Verdict and recommendation are pack-defined, so they are strings here
    and checked against the pack by Finding validation, which can name the allowed values in its reasons."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: str
    confidence: Confidence
    summary: str
    recommendation: str
    evidence: list[str] = Field(default_factory=list)


class Review(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    approved: bool
    reasons: list[str] = Field(default_factory=list)


class AgentUsage(BaseModel):
    """One agent call's model and tokens, kept for the Audit Entry and cost reporting."""

    model_config = ConfigDict(frozen=True)

    agent: str
    prompt_version: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost: float = 0.0
    latency_ms: int = 0


class AnalystDecision(BaseModel):
    """A named analyst's ruling. The action may differ from the Finding's recommendation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: DecisionOutcome
    action: str = Field(min_length=1, max_length=64)
    analyst_id: str = Field(min_length=1, max_length=128)
    notes: str = Field(default="", max_length=4000)
