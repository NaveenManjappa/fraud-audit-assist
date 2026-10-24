"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-10-24 20:17:46.276695
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "alerts",
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("alert_id"),
        sa.UniqueConstraint("account_id", "sequence", name="uq_alerts_account_sequence"),
    )
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.create_index("ix_alerts_band_score", ["band", "score"], unique=False)

    op.create_table(
        "audit_entries",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("model", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=64), nullable=True),
        sa.Column("rules_version", sa.String(length=32), nullable=True),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("cost", sa.Float(), nullable=False),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_audit_entries_case_id"), ["case_id"], unique=False)

    op.create_table(
        "confirmed_frauds",
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("account_id"),
    )
    op.create_table(
        "events",
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("device_id", sa.String(length=256), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=True),
        sa.Column("channel", sa.String(length=32), nullable=False),
        sa.Column("attributes", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
    )
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.create_index("ix_events_account_occurred", ["account_id", "occurred_at"], unique=False)

    op.create_table(
        "identifier_links",
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("value", sa.String(length=512), nullable=False),
        sa.Column("customer_id", sa.String(length=128), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("first_event_id", sa.Uuid(), nullable=False),
        sa.PrimaryKeyConstraint("account_id", "kind", "value"),
    )
    with op.batch_alter_table("identifier_links", schema=None) as batch_op:
        batch_op.create_index("ix_identifier_links_identifier", ["kind", "value"], unique=False)

    op.create_table(
        "cases",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("band", sa.String(length=16), nullable=False),
        sa.Column("score", sa.Integer(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("not_investigated", sa.Boolean(), nullable=False),
        sa.Column("investigation_failed", sa.Boolean(), nullable=False),
        sa.Column("review_flagged", sa.Boolean(), nullable=False),
        sa.Column("investigation_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finding", sa.JSON(), nullable=True),
        sa.Column("rejection_reasons", sa.JSON(), nullable=False),
        sa.Column("pseudonym_map", sa.JSON(), nullable=False),
        sa.Column("evidence_refs", sa.JSON(), nullable=False),
        sa.Column("usage", sa.JSON(), nullable=False),
        sa.Column("prompt_versions", sa.String(length=128), nullable=True),
        sa.Column("failure", sa.Text(), nullable=True),
        sa.Column("trace_id", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.alert_id"],
        ),
        sa.PrimaryKeyConstraint("case_id"),
        sa.UniqueConstraint("alert_id"),
    )
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_cases_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_cases_status"), ["status"], unique=False)

    op.create_table(
        "rule_hits",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("alert_id", sa.Uuid(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("rule_id", sa.String(length=32), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("evidence_event_ids", sa.JSON(), nullable=False),
        sa.Column("triggered_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("rules_version", sa.String(length=32), nullable=False),
        sa.ForeignKeyConstraint(
            ["alert_id"],
            ["alerts.alert_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("alert_id", "position", name="uq_rule_hits_alert_position"),
    )
    with op.batch_alter_table("rule_hits", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_rule_hits_alert_id"), ["alert_id"], unique=False)

    op.create_table(
        "analyst_decisions",
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("analyst_id", sa.String(length=128), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.case_id"],
        ),
        sa.PrimaryKeyConstraint("case_id"),
    )
    op.create_table(
        "interventions",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("case_id", sa.Uuid(), nullable=False),
        sa.Column("account_id", sa.String(length=128), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["case_id"],
            ["cases.case_id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("interventions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_interventions_account_id"), ["account_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_interventions_case_id"), ["case_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("interventions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_interventions_case_id"))
        batch_op.drop_index(batch_op.f("ix_interventions_account_id"))

    op.drop_table("interventions")
    op.drop_table("analyst_decisions")
    with op.batch_alter_table("rule_hits", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_rule_hits_alert_id"))

    op.drop_table("rule_hits")
    with op.batch_alter_table("cases", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_cases_status"))
        batch_op.drop_index(batch_op.f("ix_cases_account_id"))

    op.drop_table("cases")
    with op.batch_alter_table("identifier_links", schema=None) as batch_op:
        batch_op.drop_index("ix_identifier_links_identifier")

    op.drop_table("identifier_links")
    with op.batch_alter_table("events", schema=None) as batch_op:
        batch_op.drop_index("ix_events_account_occurred")

    op.drop_table("events")
    op.drop_table("confirmed_frauds")
    with op.batch_alter_table("audit_entries", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_audit_entries_case_id"))

    op.drop_table("audit_entries")
    with op.batch_alter_table("alerts", schema=None) as batch_op:
        batch_op.drop_index("ix_alerts_band_score")

    op.drop_table("alerts")
