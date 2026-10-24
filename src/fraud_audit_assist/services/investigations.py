"""Running Investigations and applying Analyst Decisions.

- `InvestigationRunner` runs a Case's workflow thread to Await Approval and records the outcome on the Case.
- `InvestigationWorker` takes Cases off the queue one at a time, behind the budget guard (ADR-0004).
- `DecisionService` resumes a paused thread with the analyst's decision.
- `ActService` is the workflow's Act step: it records the decision, Intervention and Audit Entries in one transaction.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from fraud_audit_assist.agents.agents import INVESTIGATOR, REVIEWER
from fraud_audit_assist.agents.evidence import EvidenceLedger
from fraud_audit_assist.agents.workflow import InvestigationState
from fraud_audit_assist.config import InvestigationSettings
from fraud_audit_assist.domain.cases import AgentUsage, AnalystDecision, CaseStatus, DecisionOutcome
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.errors import ConflictError, InvalidInputError, InvestigationError, NotFoundError
from fraud_audit_assist.observability.logging import get_logger
from fraud_audit_assist.observability.metrics import Metrics
from fraud_audit_assist.observability.tracing import current_trace_id, tracer
from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.persistence.repositories import AuditRepository, CaseRepository
from fraud_audit_assist.persistence.tables import AnalystDecisionRow, AuditEntryRow, InterventionRow
from fraud_audit_assist.services.case_queue import CaseQueue
from fraud_audit_assist.services.ingestion import IngestionService

log = get_logger(__name__)

Clock = Callable[[], datetime]
WorkflowGraph = CompiledStateGraph[Any, Any, Any, Any]


def _config(case_id: uuid.UUID) -> RunnableConfig:
    # The Case is the thread: one Investigation per Case, resumable by Case id alone.
    return {"configurable": {"thread_id": str(case_id)}}


class InvestigationRunner:
    def __init__(
        self,
        db: Database,
        graph: WorkflowGraph,
        pack: LoadedPack,
        metrics: Metrics,
        prompt_versions: str,
        clock: Clock,
    ) -> None:
        self._db = db
        self._graph = graph
        self._pack = pack
        self._metrics = metrics
        self._prompt_versions = prompt_versions
        self._clock = clock

    async def run(self, case_id: uuid.UUID) -> None:
        """Runs, or resumes, the Case's thread until it waits for a decision, then records the outcome."""
        started = time.perf_counter()
        config = _config(case_id)
        with tracer().start_as_current_span("investigate case") as span:
            span.set_attribute("faa.case_id", str(case_id))
            snapshot = await self._graph.aget_state(config)
            if snapshot.next == ("await_approval",) and snapshot.interrupts:
                values = snapshot.values  # A crash after the pause but before the Case was updated.
            elif snapshot.next:
                # Interrupted mid-run: resume from the last checkpoint instead of paying for finished steps again.
                values = await self._graph.ainvoke(None, config)
            else:
                values = await self._graph.ainvoke({"case_id": str(case_id)}, config)
            state = cast(InvestigationState, values)
            if not (await self._graph.aget_state(config)).interrupts:
                raise InvestigationError(f"case {case_id}: workflow ended without an approval request")
            await self._record(case_id, state, current_trace_id())

        outcome = state["outcome"]
        self._metrics.investigations.labels(outcome).inc()
        self._metrics.investigation_seconds.observe(time.perf_counter() - started)
        log.info("investigation awaiting approval", case_id=str(case_id), outcome=outcome)

    async def _record(self, case_id: uuid.UUID, state: InvestigationState, trace_id: str | None) -> None:
        ledger = EvidenceLedger.model_validate(state["ledger"])
        usage = [AgentUsage.model_validate(u) for u in state.get("usage", [])]
        finding = state.get("finding")
        cited = ledger.event_ids_for(finding["evidence"]) if finding else {}
        now = self._clock()

        async with self._db.transaction() as session:
            case = await CaseRepository(session).get(case_id, for_update=True)
            if case is None:
                raise NotFoundError(f"case {case_id} not found")
            case.status = CaseStatus.AWAITING_APPROVAL.value
            case.finding = finding
            case.review_flagged = state["outcome"] == "flagged"
            case.investigation_failed = state["outcome"] == "failed"
            case.rejection_reasons = state.get("reasons", []) if state["outcome"] == "flagged" else []
            case.pseudonym_map = ledger.pseudonym_map
            case.evidence_refs = ledger.event_ids_by_ref
            case.usage = [u.model_dump() for u in usage]
            case.prompt_versions = self._prompt_versions
            case.failure = state.get("failure")
            case.trace_id = trace_id
            case.updated_at = now
            audit = AuditRepository(session)
            for agent, kind in ((INVESTIGATOR, "InvestigationCompleted"), (REVIEWER, "ReviewCompleted")):
                calls = [u for u in usage if u.agent == agent]
                if not calls:
                    continue
                audit.add(
                    AuditEntryRow(
                        case_id=case_id,
                        kind=kind,
                        actor=agent,
                        model=calls[-1].model,
                        prompt_version=calls[-1].prompt_version,
                        rules_version=state.get("rules_version"),
                        evidence={"cited": cited},
                        input_tokens=sum(u.input_tokens for u in calls),
                        output_tokens=sum(u.output_tokens for u in calls),
                        cost=sum(u.cost for u in calls),
                        details={
                            "outcome": state["outcome"],
                            "calls": len(calls),
                            "failure": state.get("failure"),
                        },
                        created_at=now,
                    )
                )


class InvestigationWorker:
    """Takes one Case at a time, which keeps the daily budget count free of races; assumes one API instance."""

    def __init__(
        self,
        db: Database,
        queue: CaseQueue,
        runner: InvestigationRunner,
        settings: InvestigationSettings,
        clock: Clock,
    ) -> None:
        self._db = db
        self._queue = queue
        self._runner = runner
        self._settings = settings
        self._clock = clock
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Re-queues Cases a crash left behind, then starts the loop. Called before the API accepts requests, so a
        Case opened after startup is never picked up twice."""
        async with self._db.transaction() as session:
            released = await CaseRepository(session).release_stale_decisions(self._clock())
            leftover = await CaseRepository(session).to_resume()
        if released:
            log.warning("released cases left mid-decision", count=released)
        for case_id in leftover:
            await self._queue.put(case_id)
        if leftover:
            log.info("re-queued unfinished cases", count=len(leftover))
        self._task = asyncio.create_task(self._loop(), name="investigation-worker")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def _loop(self) -> None:
        while True:
            case_id = await self._queue.get()
            try:
                await self.process(case_id)
            except Exception:
                # The Case stays Investigating; startup re-queues it, within that day's budget.
                log.exception("investigation failed to run", case_id=str(case_id))
            finally:
                self._queue.task_done()

    async def process(self, case_id: uuid.UUID) -> None:
        if not await self._admit(case_id):
            return
        await self._runner.run(case_id)

    async def _admit(self, case_id: uuid.UUID) -> bool:
        now = self._clock()
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        async with self._db.transaction() as session:
            cases = CaseRepository(session)
            case = await cases.get(case_id, for_update=True)
            if case is None or case.status not in (CaseStatus.OPEN.value, CaseStatus.INVESTIGATING.value):
                return False
            if (
                not self._settings.enabled
                or await cases.started_since(day_start) >= self._settings.daily_limit
            ):
                # Skipped on purpose and never re-queued: re-queuing would spend the budget on a replay after a restart.
                case.not_investigated = True
                case.updated_at = now
                log.warning(
                    "investigation skipped by budget guard",
                    case_id=str(case_id),
                    enabled=self._settings.enabled,
                )
                return False
            case.status = CaseStatus.INVESTIGATING.value
            case.investigation_started_at = now
            case.updated_at = now
        return True


class DecisionService:
    def __init__(self, db: Database, graph: WorkflowGraph, pack: LoadedPack, clock: Clock) -> None:
        self._db = db
        self._graph = graph
        self._pack = pack
        self._clock = clock

    async def decide(self, case_id: uuid.UUID, decision: AnalystDecision) -> None:
        if decision.action not in {a.id for a in self._pack.pack.actions}:
            allowed = ", ".join(a.id for a in self._pack.pack.actions)
            raise InvalidInputError(f"unknown action '{decision.action}'; allowed: {allowed}")
        if not await self._claim(case_id, CaseStatus.AWAITING_APPROVAL, CaseStatus.DECIDING):
            async with self._db.read() as session:
                case = await CaseRepository(session).get(case_id)
            if case is None:
                raise NotFoundError(f"case {case_id} not found")
            raise ConflictError(f"case {case_id} is {case.status}, not {CaseStatus.AWAITING_APPROVAL.value}")
        try:
            with tracer().start_as_current_span("decide case") as span:
                span.set_attribute("faa.case_id", str(case_id))
                await self._graph.ainvoke(Command(resume=decision.model_dump(mode="json")), _config(case_id))
        except BaseException:
            # Nothing Act wrote survived, so the Case goes back to waiting and the decision can be posted again.
            await self._claim(case_id, CaseStatus.DECIDING, CaseStatus.AWAITING_APPROVAL)
            raise

    async def _claim(self, case_id: uuid.UUID, expected: CaseStatus, new: CaseStatus) -> bool:
        async with self._db.transaction() as session:
            return await CaseRepository(session).claim(case_id, expected, new, self._clock())


class ActService:
    """Records what the analyst decided. It enforces nothing: the Intervention is for the source system to enforce."""

    def __init__(
        self, db: Database, pack: LoadedPack, ingestion: IngestionService, queue: CaseQueue, clock: Clock
    ) -> None:
        self._db = db
        self._pack = pack
        self._ingestion = ingestion
        self._queue = queue
        self._clock = clock

    async def act(self, case_id: uuid.UUID, decision: AnalystDecision, state: InvestigationState) -> None:
        async def work(session: AsyncSession) -> list[uuid.UUID]:
            return await self._act_in(session, case_id, decision, state)

        try:
            opened = await self._db.run_transaction(work)
        except IntegrityError as error:
            raise ConflictError(f"case {case_id} already has a decision") from error
        for other in opened:
            await self._queue.put(other)

    async def _act_in(
        self, session: AsyncSession, case_id: uuid.UUID, decision: AnalystDecision, state: InvestigationState
    ) -> list[uuid.UUID]:
        now = self._clock()
        cases = CaseRepository(session)
        case = await cases.get(case_id, for_update=True)
        if case is None:
            raise NotFoundError(f"case {case_id} not found")
        session.add(
            AnalystDecisionRow(
                case_id=case_id,
                outcome=decision.outcome.value,
                action=decision.action,
                analyst_id=decision.analyst_id,
                notes=decision.notes,
                decided_at=now,
            )
        )
        # Flushed now so a concurrent second decision fails here, before anything reaches the Link Graph.
        await session.flush()

        action = self._pack.pack.action(decision.action)
        until = now + timedelta(days=action.duration_days) if action.duration_days else None
        if action.records_intervention:
            session.add(
                InterventionRow(
                    case_id=case_id, account_id=case.account_id, action=action.id, until=until, created_at=now
                )
            )
        audit = AuditRepository(session)
        finding = state.get("finding") or {}
        audit.add(
            AuditEntryRow(
                case_id=case_id,
                kind="DecisionRecorded",
                actor=decision.analyst_id,
                rules_version=state.get("rules_version"),
                details={
                    "outcome": decision.outcome.value,
                    "action": decision.action,
                    "notes": decision.notes,
                    "recommended": finding.get("recommendation"),
                    "agreed_with_recommendation": finding.get("recommendation") == decision.action,
                },
                created_at=now,
            )
        )
        audit.add(
            AuditEntryRow(
                case_id=case_id,
                kind="ActionTaken",
                actor=decision.analyst_id,
                details={"action": action.id, "until": until.isoformat() if until else None},
                created_at=now,
            )
        )
        case.status = CaseStatus.CLOSED.value
        case.updated_at = now

        if decision.outcome is DecisionOutcome.CONFIRMED_FRAUD:
            return await self._ingestion.confirm_fraud_in(session, case.account_id, now, "Analyst")
        return []


def utc_now() -> datetime:
    return datetime.now(UTC)
