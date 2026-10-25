"""Detection's output: Rule Hits grouped into Alerts, one per Risk Episode (ADR-0013)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from fraud_audit_assist.domain.events import AwareDatetime
from fraud_audit_assist.domain.rules import BandThresholds, RiskBand


class RuleHit(BaseModel):
    """One Rule matching on an Account. Never rewritten once stored.

    `evidence_event_ids` ends with the event that completed the pattern, whose time is `triggered_at`.
    """

    model_config = ConfigDict(frozen=True)

    rule_id: str
    points: int
    evidence_event_ids: list[UUID] = Field(min_length=1)
    triggered_at: AwareDatetime
    explanation: str


class Alert(BaseModel):
    """Detection's verdict on one Risk Episode of an Account. Once stored it only grows."""

    model_config = ConfigDict(frozen=True)

    account_id: str
    rules_version: str
    hits: list[RuleHit] = Field(min_length=1)
    score: int
    band: RiskBand

    @property
    def opened_at(self) -> datetime:
        return self.hits[0].triggered_at

    def hit_reaching(self, band: RiskBand, bands: BandThresholds) -> RuleHit | None:
        """The hit whose points first lifted the running Score into `band` or above, if any did.

        Hits are in the order they were added and the Score only grows, so this is when an analyst first saw it.
        """
        running = 0
        for hit in self.hits:
            running += hit.points
            if bands.band_for(running) >= band:
                return hit
        return None
