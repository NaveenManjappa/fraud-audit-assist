# ADR-0019: A decision claims the Case before resuming its thread

Status: Accepted | Date: 2026-10-24

`POST /v1/cases/{id}/decision` first moves the Case from AwaitingApproval to Deciding with a conditional update, so
of two concurrent decisions exactly one proceeds and the other gets 409 without touching the workflow. It then
resumes the thread. Act writes the Analyst Decision (one per Case, enforced by its primary key), the Intervention
(for the source system to enforce; this service enforces nothing), the DecisionRecorded and ActionTaken Audit
Entries (with whether the analyst agreed with the recommendation) and closes the Case, all in one transaction. A
ConfirmedFraud outcome also records a Confirmed Fraud and re-scores the Account's Neighbours in that transaction.

If anything fails, nothing is kept and the Case returns to AwaitingApproval, so the decision can be posted again; a
Case a crash left in Deciding is released at startup.
