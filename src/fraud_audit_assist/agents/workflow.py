"""The Investigation as a LangGraph workflow (ADR-0005).

    enrich -> investigate -> validate -> review -> finalize -> await_approval -> act
                  ^             |           |
                  +---- retry <-+-----------+   (one retry, with the reasons)

Code does everything that can be done in code: enrich builds the masked brief, validate checks refs and the contract,
finalize decides the outcome. The agents only investigate and review. Every path reaches await_approval, so a risky
Account is never lost because the AI failed; the analyst sees a Review Flag or InvestigationFailed instead.

State holds only JSON-friendly values, the Evidence Ledger included, so every step is checkpointed and an
interrupted Investigation resumes where it stopped. await_approval pauses with `interrupt()` until an analyst's
decision resumes the thread; act then records it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from fraud_audit_assist.agents.agents import AgentCallError, Investigator, ReplyParseError, Reviewer
from fraud_audit_assist.agents.evidence import EvidenceLedger
from fraud_audit_assist.agents.source import InvestigationSource
from fraud_audit_assist.agents.tools import investigator_tools
from fraud_audit_assist.agents.validation import check_finding, is_complete
from fraud_audit_assist.domain.cases import AnalystDecision, Finding
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.observability.logging import get_logger

log = get_logger(__name__)

Outcome = Literal["approved", "flagged", "failed"]

NOT_A_FINDING = (
    "Your reply was not a Finding: reply with one JSON object in the required shape and nothing else."
)


class InvestigationState(TypedDict, total=False):
    case_id: str
    account_id: str
    rules_version: str
    brief: str
    ledger: dict[str, Any]
    attempt: int
    reasons: list[str]
    finding: dict[str, Any] | None
    parse_failed: bool
    approved: bool
    attempts: list[dict[str, Any]]
    usage: list[dict[str, Any]]
    outcome: Outcome
    failure: str | None
    decision: dict[str, Any]


class ActHandler(Protocol):
    async def act(self, case_id: uuid.UUID, decision: AnalystDecision, state: InvestigationState) -> None: ...


@dataclass(frozen=True)
class WorkflowDeps:
    pack: DomainPack
    source: InvestigationSource
    investigator: Investigator
    reviewer: Reviewer
    act: ActHandler
    max_attempts: int = 2


def approval_request(state: InvestigationState) -> dict[str, Any]:
    """What the analyst is asked to decide on. The Finding stays masked here; the API unmasks it for the analyst."""
    return {
        "case_id": state["case_id"],
        "outcome": state["outcome"],
        "finding": state.get("finding"),
        "review_flagged": state["outcome"] == "flagged",
        "investigation_failed": state["outcome"] == "failed",
        "rejection_reasons": state.get("reasons", []) if state["outcome"] == "flagged" else [],
        "failure": state.get("failure"),
    }


def build_investigation_graph(deps: WorkflowDeps) -> StateGraph[InvestigationState]:
    pack = deps.pack

    async def enrich(state: InvestigationState) -> InvestigationState:
        context = await deps.source.case_context(uuid.UUID(state["case_id"]))
        ledger = EvidenceLedger()
        cited = [i for hit in context.alert.hits for i in hit.evidence_event_ids]
        event_lines = [ledger.describe_event(e, pack) for e in await deps.source.events(cited)]
        hit_lines = []
        for hit in context.alert.hits:
            refs = ", ".join(
                ledger.ref_by_event[str(i)] for i in hit.evidence_event_ids if str(i) in ledger.ref_by_event
            )
            hit_lines.append(
                f"- {hit.rule_id} (+{hit.points}) {ledger.mask(hit.explanation)}. Evidence: {refs}"
            )
        account = ledger.register("ACCOUNT", context.account_id) or "withheld"
        brief = "\n".join(
            [
                f"Case for {pack.terminology.account} {account}.",
                f"Alert Score: {context.alert.score}. Risk Band: {context.alert.band.value}. "
                f"Rules version: {context.alert.rules_version}.",
                "Rule Hits:",
                *hit_lines,
                "Evidence events:",
                *event_lines,
            ]
        )
        return {
            "account_id": context.account_id,
            "rules_version": context.alert.rules_version,
            "brief": brief,
            "ledger": ledger.model_dump(mode="json"),
            "attempt": 1,
            "reasons": [],
            "finding": None,
            "parse_failed": False,
            "approved": False,
            "attempts": [],
            "usage": [],
            "failure": None,
        }

    async def investigate(state: InvestigationState) -> InvestigationState:
        context = await deps.source.case_context(uuid.UUID(state["case_id"]))
        ledger = EvidenceLedger.model_validate(state["ledger"])
        tools = investigator_tools(context, deps.source, ledger, pack)
        try:
            result = await deps.investigator.investigate(state["brief"], state["reasons"], tools)
        except AgentCallError as error:
            return {
                "ledger": ledger.model_dump(mode="json"),
                "usage": state["usage"] + [u.model_dump(mode="json") for u in error.usage],
                "failure": f"Investigator: {error}",
            }
        update: InvestigationState = {
            "ledger": ledger.model_dump(mode="json"),
            "usage": state["usage"] + [u.model_dump(mode="json") for u in result.usage],
            "approved": False,
        }
        if result.finding is None:
            log.warning("investigator reply unparseable", case_id=state["case_id"], error=result.parse_error)
            return {**update, "parse_failed": True, "reasons": [NOT_A_FINDING]}
        return {**update, "parse_failed": False, "finding": result.finding.model_dump(mode="json")}

    async def validate(state: InvestigationState) -> InvestigationState:
        finding = Finding.model_validate(state["finding"])
        ledger = EvidenceLedger.model_validate(state["ledger"])
        reasons = check_finding(finding, ledger, pack)
        if not reasons:
            return {"reasons": []}
        attempt: dict[str, Any] = {
            "finding": state["finding"],
            "unknown_refs": ledger.unknown_refs(finding.evidence),
            "approved": None,
        }
        return {"reasons": reasons, "attempts": [*state["attempts"], attempt]}

    async def review(state: InvestigationState) -> InvestigationState:
        finding = Finding.model_validate(state["finding"])
        ledger = EvidenceLedger.model_validate(state["ledger"])
        try:
            result = await deps.reviewer.review(finding, ledger.lines_for(finding.evidence))
        except (AgentCallError, ReplyParseError) as error:
            # A Reviewer that cannot answer leaves the Finding unchecked, and an unchecked Finding must not be shown
            # as if it were reviewed.
            return {"failure": f"Reviewer: {error}"}
        attempt: dict[str, Any] = {
            "finding": state["finding"],
            "unknown_refs": [],
            "approved": result.review.approved,
        }
        usage = state["usage"] + [u.model_dump(mode="json") for u in result.usage]
        if result.review.approved:
            return {
                "approved": True,
                "reasons": [],
                "attempts": [*state["attempts"], attempt],
                "usage": usage,
            }
        reasons = result.review.reasons or ["The Reviewer rejected the Finding without a reason."]
        return {
            "approved": False,
            "reasons": reasons,
            "attempts": [*state["attempts"], attempt],
            "usage": usage,
        }

    async def retry(state: InvestigationState) -> InvestigationState:
        return {"attempt": state["attempt"] + 1}

    async def finalize(state: InvestigationState) -> InvestigationState:
        if state.get("failure"):
            return {"outcome": "failed", "finding": None}
        if state.get("parse_failed"):
            return {
                "outcome": "failed",
                "finding": None,
                "failure": "The Investigator's reply was unparseable twice.",
            }
        if state.get("approved"):
            return {"outcome": "approved"}
        finding = Finding.model_validate(state["finding"])
        if not is_complete(finding, pack):
            reasons = " ".join(state.get("reasons", []))
            return {
                "outcome": "failed",
                "finding": None,
                "failure": f"The last Finding was incomplete: {reasons}",
            }
        return {"outcome": "flagged"}

    async def await_approval(state: InvestigationState) -> InvestigationState:
        # Re-runs from the top when resumed, so nothing before interrupt() may have side effects.
        decision = interrupt(approval_request(state))
        return {"decision": AnalystDecision.model_validate(decision).model_dump(mode="json")}

    async def act(state: InvestigationState) -> InvestigationState:
        await deps.act.act(
            uuid.UUID(state["case_id"]), AnalystDecision.model_validate(state["decision"]), state
        )
        return {}

    def after_investigate(state: InvestigationState) -> str:
        if state.get("failure"):
            return "finalize"
        if state.get("parse_failed"):
            return "retry" if state["attempt"] < deps.max_attempts else "finalize"
        return "validate"

    def after_check(state: InvestigationState) -> str:
        if state.get("failure") or state.get("approved"):
            return "finalize"
        if state.get("reasons"):
            return "retry" if state["attempt"] < deps.max_attempts else "finalize"
        return "review"

    graph = StateGraph(InvestigationState)
    for name, node in (
        ("enrich", enrich),
        ("investigate", investigate),
        ("validate", validate),
        ("review", review),
        ("retry", retry),
        ("finalize", finalize),
        ("await_approval", await_approval),
        ("act", act),
    ):
        graph.add_node(name, node)
    graph.add_edge(START, "enrich")
    graph.add_edge("enrich", "investigate")
    graph.add_conditional_edges("investigate", after_investigate, ["retry", "finalize", "validate"])
    graph.add_conditional_edges("validate", after_check, ["review", "retry", "finalize"])
    graph.add_conditional_edges("review", after_check, ["finalize", "retry"])
    graph.add_edge("retry", "investigate")
    graph.add_edge("finalize", "await_approval")
    graph.add_edge("await_approval", "act")
    graph.add_edge("act", END)
    return graph
