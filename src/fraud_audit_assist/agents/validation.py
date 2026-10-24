"""The checks code makes on a Finding before any Reviewer call (ADR-0006).

They are certain and free in code, so no model is paid to make them. A failure is a rejection like the Reviewer's,
with reasons the Investigator can act on.
"""

from __future__ import annotations

from fraud_audit_assist.agents.evidence import EvidenceLedger
from fraud_audit_assist.agents.prompts import MAX_SUMMARY_WORDS
from fraud_audit_assist.domain.cases import Finding
from fraud_audit_assist.domain.packs import DomainPack


def check_finding(finding: Finding, ledger: EvidenceLedger, pack: DomainPack) -> list[str]:
    """Why the Finding is rejected; empty when it passes."""
    reasons: list[str] = []
    actions = {a.id: a for a in pack.actions}

    if finding.verdict not in pack.verdicts:
        reasons.append(f"The verdict must be one of {', '.join(pack.verdicts)}.")
    if finding.recommendation not in actions:
        reasons.append(f"The recommendation must be one of {', '.join(actions)}.")

    words = len(finding.summary.split())
    if words == 0:
        reasons.append("The summary is missing.")
    elif words > MAX_SUMMARY_WORDS:
        reasons.append(f"The summary is {words} words; it must be at most {MAX_SUMMARY_WORDS}.")

    if not finding.evidence:
        reasons.append("The Finding cites no evidence; cite the Evidence Refs the summary relies on.")
    elif unknown := ledger.unknown_refs(finding.evidence):
        reasons.append(
            f"These Evidence Refs were never given to you: {', '.join(unknown)}. Cite only refs you were shown."
        )

    # A guardrail, not a style rule: blocking or adding friction to a customer the agent itself calls genuine or
    # cannot judge is the costliest mistake it can recommend.
    action = actions.get(finding.recommendation)
    fraud_verdicts = {t.id for t in pack.typologies}
    if action is not None and action.restrictive and finding.verdict not in fraud_verdicts:
        reasons.append(
            f"{action.id} is restrictive and needs a fraud verdict; with {finding.verdict} recommend a non-restrictive action."
        )
    return reasons


def is_complete(finding: Finding, pack: DomainPack) -> bool:
    """Whether every field the analyst's view and the Audit Entry read is inside the contract. A Finding that is
    not is never kept, not even with the Review Flag: there would be nothing sound to show or audit."""
    return (
        finding.verdict in pack.verdicts
        and finding.recommendation in {a.id for a in pack.actions}
        and bool(finding.summary.strip())
    )
