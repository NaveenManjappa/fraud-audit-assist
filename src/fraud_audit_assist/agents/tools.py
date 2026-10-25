"""The Investigator's tools, bound in code to the Case's Account (ADR-0007).

They take no account argument, so the model cannot be talked into reading another customer's data. Every result is
written through the Evidence Ledger, which masks it and issues refs the Finding may cite.
"""

from __future__ import annotations

from datetime import timedelta

from langchain_core.tools import BaseTool, StructuredTool
from pydantic import BaseModel, Field

from fraud_audit_assist.agents.evidence import EvidenceLedger, compute_baseline
from fraud_audit_assist.agents.source import CaseContext, InvestigationSource
from fraud_audit_assist.domain.packs import DomainPack

# Bounds tokens: a longer look-back adds cost faster than it adds evidence.
MAX_TIMELINE_DAYS = 90
MAX_TIMELINE_EVENTS = 200


class TimelineArgs(BaseModel):
    days: int = Field(description="How many days back from the latest event to return (1 to 90).")


class NoArgs(BaseModel):
    pass


def investigator_tools(
    context: CaseContext, source: InvestigationSource, ledger: EvidenceLedger, pack: DomainPack
) -> list[BaseTool]:
    account_term = pack.terminology.account
    customer_term = pack.terminology.customer

    async def get_account_timeline(days: int) -> str:
        days = max(1, min(days, MAX_TIMELINE_DAYS))
        events = await source.timeline(
            context.account_id, context.as_of - timedelta(days=days), context.as_of
        )
        shown = events[-MAX_TIMELINE_EVENTS:]
        lines = [ledger.describe_event(e, pack) for e in shown]
        header = f"{len(events)} events in the last {days} days" + (
            f"; showing the latest {len(shown)}" if len(shown) < len(events) else ""
        )
        return "\n".join([header, *lines])

    async def query_graph_neighbourhood() -> str:
        identifiers = await source.neighbourhood(context.account_id)
        if not identifiers:
            return f"The {account_term} has no linking identifiers on record."
        return "\n".join(ledger.describe_identifier(i, pack) for i in identifiers)

    async def get_customer_baseline() -> str:
        own = {e.event_id for e in await source.timeline(context.account_id, None, context.as_of)}
        cited = [i for hit in context.alert.hits for i in hit.evidence_event_ids if i in own]
        flagged = await source.events(cited)
        before = min((e.occurred_at for e in flagged), default=context.as_of)
        history = await source.timeline(context.account_id, None, before)
        return ledger.describe_baseline(compute_baseline(history, before), pack)

    return [
        StructuredTool.from_function(
            coroutine=get_account_timeline,
            name="get_account_timeline",
            description=f"The {account_term}'s events in the last `days` days up to its latest event. Ask only for the days you need.",
            args_schema=TimelineArgs,
        ),
        StructuredTool.from_function(
            coroutine=query_graph_neighbourhood,
            name="query_graph_neighbourhood",
            description=(
                f"Each of the {account_term}'s linking identifiers and the other {account_term}s that use it, "
                "marked when they are Confirmed Fraud."
            ),
            args_schema=NoArgs,
        ),
        StructuredTool.from_function(
            coroutine=get_customer_baseline,
            name="get_customer_baseline",
            description=(
                f"The {customer_term}'s usual countries, devices, channels and activity, from before the flagged "
                "activity began."
            ),
            args_schema=NoArgs,
        ),
    ]
