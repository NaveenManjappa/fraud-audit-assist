"""HTTP endpoints. Thin: parse, call one service or repository, shape the response."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response, status
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from fraud_audit_assist.agents.evidence import EvidenceLedger
from fraud_audit_assist.api.schemas import (
    AlertOut,
    AuditEntryOut,
    CaseOut,
    CaseSummaryOut,
    ConfirmedFraudIn,
    DecisionIn,
    DecisionOut,
    FindingOut,
    IngestRequest,
    IngestResponse,
    InterventionOut,
    LinkedIdentifierOut,
    RuleHitOut,
    RulesOut,
)
from fraud_audit_assist.api.security import require
from fraud_audit_assist.container import Container
from fraud_audit_assist.domain.cases import CaseStatus
from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.errors import NotFoundError
from fraud_audit_assist.graph.neighbourhood import neighbourhood
from fraud_audit_assist.persistence.repositories import (
    AlertRepository,
    AuditRepository,
    CaseRepository,
    EventRepository,
    LinkRepository,
)
from fraud_audit_assist.persistence.tables import AlertRow, CaseRow

Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]

health = APIRouter(tags=["operations"])
v1 = APIRouter(prefix="/v1")
ingest = APIRouter(dependencies=[Depends(require("ingest"))], tags=["ingestion"])
analyst = APIRouter(dependencies=[Depends(require("analyst"))], tags=["analyst"])


def container(request: Request) -> Container:
    value: Container = request.app.state.container
    return value


Deps = Annotated[Container, Depends(container)]


@health.get("/health/live")
async def live() -> dict[str, str]:
    return {"status": "ok"}


@health.get("/health/ready")
async def ready(c: Deps, response: Response) -> dict[str, str]:
    try:
        await c.db.ping()
    except Exception as error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {"status": "unavailable", "database": type(error).__name__}
    return {"status": "ok", "pack": c.pack.pack.id, "rules_version": c.pack.rules.version}


@health.get("/metrics", include_in_schema=False)
async def metrics(c: Deps) -> Response:
    return Response(generate_latest(c.metrics.registry), media_type=CONTENT_TYPE_LATEST)


@ingest.post("/events", response_model=IngestResponse)
async def post_events(body: IngestRequest, c: Deps) -> IngestResponse:
    result = await c.ingestion.ingest(body.events)
    return IngestResponse(**result.__dict__)


@analyst.post("/accounts/{account_id}/confirmed-fraud", status_code=status.HTTP_202_ACCEPTED)
async def confirm_fraud(account_id: str, body: ConfirmedFraudIn, c: Deps) -> dict[str, list[uuid.UUID]]:
    """Seeds a Confirmed Fraud known from outside this service, such as a customer's report."""
    return {"opened_cases": await c.ingestion.confirm_fraud(account_id, body.decided_at, "Seed")}


@analyst.get("/accounts/{account_id}/timeline", response_model=list[Event])
async def timeline(account_id: str, c: Deps) -> list[Event]:
    async with c.db.read() as session:
        return await EventRepository(session).timeline(account_id)


@analyst.get("/accounts/{account_id}/neighbourhood", response_model=list[LinkedIdentifierOut])
async def account_neighbourhood(account_id: str, c: Deps) -> list[LinkedIdentifierOut]:
    async with c.db.read() as session:
        linked = await neighbourhood(account_id, c.pack.pack, LinkRepository(session))
    return [LinkedIdentifierOut.model_validate(i.model_dump()) for i in linked]


@analyst.get("/alerts", response_model=list[AlertOut])
async def list_alerts(
    c: Deps,
    band: RiskBand | None = None,
    account_id: str | None = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> list[AlertOut]:
    async with c.db.read() as session:
        rows = await AlertRepository(session).page(band, account_id, limit, offset)
    return [_alert(r) for r in rows]


@analyst.get("/alerts/{alert_id}", response_model=AlertOut)
async def get_alert(alert_id: uuid.UUID, c: Deps) -> AlertOut:
    async with c.db.read() as session:
        row = await session.get(AlertRow, alert_id)
        stored = await AlertRepository(session).get(alert_id)
    if row is None or stored is None:
        raise NotFoundError(f"alert {alert_id} not found")
    return _alert(row, [RuleHitOut.model_validate(h.model_dump()) for h in stored.alert.hits])


@analyst.get("/cases", response_model=list[CaseSummaryOut])
async def list_cases(
    c: Deps, status: CaseStatus | None = None, limit: Limit = 50, offset: Offset = 0
) -> list[CaseSummaryOut]:
    """The queue, worst first: by Risk Band, then Score, then time opened."""
    async with c.db.read() as session:
        rows = await CaseRepository(session).page(status, limit, offset)
    return [CaseSummaryOut.model_validate(r, from_attributes=True) for r in rows]


@analyst.get("/cases/{case_id}", response_model=CaseOut)
async def get_case(case_id: uuid.UUID, c: Deps) -> CaseOut:
    async with c.db.read() as session:
        cases = CaseRepository(session)
        row = await cases.get(case_id)
        if row is None:
            raise NotFoundError(f"case {case_id} not found")
        decision = await cases.decision(case_id)
        interventions = await cases.interventions(case_id)
    return CaseOut(
        **CaseSummaryOut.model_validate(row, from_attributes=True).model_dump(),
        finding=_unmasked_finding(row),
        rejection_reasons=_unmask_all(row, row.rejection_reasons),
        failure=row.failure,
        prompt_versions=row.prompt_versions,
        usage=row.usage,
        trace_id=row.trace_id,
        decision=DecisionOut.model_validate(decision, from_attributes=True) if decision else None,
        interventions=[InterventionOut.model_validate(i, from_attributes=True) for i in interventions],
    )


@analyst.post("/cases/{case_id}/decision", response_model=CaseOut)
async def decide(case_id: uuid.UUID, body: DecisionIn, c: Deps) -> CaseOut:
    await c.decisions.decide(case_id, body)
    return await get_case(case_id, c)


@analyst.get("/cases/{case_id}/audit", response_model=list[AuditEntryOut])
async def audit(case_id: uuid.UUID, c: Deps) -> list[AuditEntryOut]:
    async with c.db.read() as session:
        rows = await AuditRepository(session).for_case(case_id)
    return [AuditEntryOut.model_validate(r, from_attributes=True) for r in rows]


@analyst.get("/rules", response_model=RulesOut)
async def rules(c: Deps) -> RulesOut:
    ruleset = c.pack.rules
    return RulesOut(
        pack=c.pack.pack.id,
        version=ruleset.version,
        rules=[r.model_dump(mode="json") for r in ruleset.rules],
        bands=ruleset.bands.model_dump(),
        risk_period_days=ruleset.risk_period.total_seconds() / 86400,
    )


def _alert(row: AlertRow, hits: list[RuleHitOut] | None = None) -> AlertOut:
    return AlertOut(
        alert_id=row.alert_id,
        account_id=row.account_id,
        score=row.score,
        band=row.band,
        rules_version=row.rules_version,
        opened_at=row.opened_at,
        updated_at=row.updated_at,
        hits=hits,
    )


def _ledger(row: CaseRow) -> EvidenceLedger:
    return EvidenceLedger(pseudonym_by_value={value: p for p, value in row.pseudonym_map.items()})


def _unmasked_finding(row: CaseRow) -> FindingOut | None:
    if not row.finding:
        return None
    ledger = _ledger(row)
    refs = row.evidence_refs
    return FindingOut(
        verdict=row.finding["verdict"],
        confidence=row.finding["confidence"],
        summary=ledger.unmask(row.finding["summary"]),
        recommendation=row.finding["recommendation"],
        evidence={ref: [uuid.UUID(i) for i in refs.get(ref, [])] for ref in row.finding["evidence"]},
    )


def _unmask_all(row: CaseRow, texts: list[str]) -> list[str]:
    ledger = _ledger(row)
    return [ledger.unmask(t) for t in texts]


v1.include_router(ingest)
v1.include_router(analyst)
