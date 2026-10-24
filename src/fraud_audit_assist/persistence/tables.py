"""The relational schema. Portable across PostgreSQL (production) and SQLite (development, tests)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, ClassVar

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Dialect,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class UtcDateTime(TypeDecorator[datetime]):
    """Always stores and returns timezone-aware UTC.

    SQLite drops the offset and returns naive datetimes, which would then compare wrongly against aware ones.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetimes are not stored; attach a timezone")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class Base(DeclarativeBase):
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        datetime: UtcDateTime(),
        dict[str, Any]: JSON,
        list[Any]: JSON,
    }


class EventRow(Base):
    """Append-only events, idempotent on `event_id` (ADR-0010)."""

    __tablename__ = "events"

    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    account_id: Mapped[str] = mapped_column(String(128))
    customer_id: Mapped[str] = mapped_column(String(128))
    type: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime]
    received_at: Mapped[datetime]
    device_id: Mapped[str | None] = mapped_column(String(256))
    ip_address: Mapped[str | None] = mapped_column(String(64))
    country: Mapped[str | None] = mapped_column(String(2))
    channel: Mapped[str] = mapped_column(String(32))
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON)

    __table_args__ = (Index("ix_events_account_occurred", "account_id", "occurred_at"),)


class IdentifierLinkRow(Base):
    """The Link Graph: one row per Account per Linking Identifier, keeping the earliest use (ADR-0015)."""

    __tablename__ = "identifier_links"

    account_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[str] = mapped_column(String(512), primary_key=True)
    customer_id: Mapped[str] = mapped_column(String(128))
    first_seen: Mapped[datetime]
    first_event_id: Mapped[uuid.UUID] = mapped_column(Uuid)

    __table_args__ = (Index("ix_identifier_links_identifier", "kind", "value"),)


class AlertRow(Base):
    __tablename__ = "alerts"

    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    account_id: Mapped[str] = mapped_column(String(128))
    sequence: Mapped[int] = mapped_column(Integer)
    rules_version: Mapped[str] = mapped_column(String(32))
    score: Mapped[int] = mapped_column(Integer)
    band: Mapped[str] = mapped_column(String(16))
    opened_at: Mapped[datetime]
    updated_at: Mapped[datetime]

    __table_args__ = (
        UniqueConstraint("account_id", "sequence", name="uq_alerts_account_sequence"),
        Index("ix_alerts_band_score", "band", "score"),
    )


class RuleHitRow(Base):
    """Append-only. `rules_version` records the Rule Set that produced this hit, which can be newer than the Alert's."""

    __tablename__ = "rule_hits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("alerts.alert_id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    rule_id: Mapped[str] = mapped_column(String(32))
    points: Mapped[int] = mapped_column(Integer)
    evidence_event_ids: Mapped[list[Any]] = mapped_column(JSON)
    triggered_at: Mapped[datetime]
    explanation: Mapped[str] = mapped_column(Text)
    rules_version: Mapped[str] = mapped_column(String(32))

    __table_args__ = (UniqueConstraint("alert_id", "position", name="uq_rule_hits_alert_position"),)


class CaseRow(Base):
    __tablename__ = "cases"

    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    alert_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("alerts.alert_id"), unique=True)
    account_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    band: Mapped[str] = mapped_column(String(16))
    score: Mapped[int] = mapped_column(Integer)
    opened_at: Mapped[datetime]
    updated_at: Mapped[datetime]
    not_investigated: Mapped[bool] = mapped_column(Boolean, default=False)
    investigation_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    review_flagged: Mapped[bool] = mapped_column(Boolean, default=False)
    investigation_started_at: Mapped[datetime | None]
    finding: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    rejection_reasons: Mapped[list[Any]] = mapped_column(JSON, default=list)
    pseudonym_map: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evidence_refs: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    usage: Mapped[list[Any]] = mapped_column(JSON, default=list)
    prompt_versions: Mapped[str | None] = mapped_column(String(128))
    failure: Mapped[str | None] = mapped_column(Text)
    trace_id: Mapped[str | None] = mapped_column(String(32))


class AnalystDecisionRow(Base):
    """One per Case; the primary key is what stops two decisions on one Case."""

    __tablename__ = "analyst_decisions"

    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.case_id"), primary_key=True)
    outcome: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    analyst_id: Mapped[str] = mapped_column(String(128))
    notes: Mapped[str] = mapped_column(Text)
    decided_at: Mapped[datetime]


class InterventionRow(Base):
    """The instruction recorded for the source system to enforce; this service enforces nothing itself."""

    __tablename__ = "interventions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("cases.case_id"), index=True)
    account_id: Mapped[str] = mapped_column(String(128), index=True)
    action: Mapped[str] = mapped_column(String(64))
    until: Mapped[datetime | None]
    created_at: Mapped[datetime]


class ConfirmedFraudRow(Base):
    __tablename__ = "confirmed_frauds"

    account_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    decided_at: Mapped[datetime]
    source: Mapped[str] = mapped_column(String(16))


class AuditEntryRow(Base):
    """Append-only record of each step after detection. Never updated or deleted."""

    __tablename__ = "audit_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    kind: Mapped[str] = mapped_column(String(32))
    actor: Mapped[str] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(64))
    rules_version: Mapped[str | None] = mapped_column(String(32))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    cost: Mapped[float] = mapped_column(Float, default=0.0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime]
