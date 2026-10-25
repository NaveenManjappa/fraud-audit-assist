from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from langgraph.checkpoint.memory import InMemorySaver
from pydantic import SecretStr

from fraud_audit_assist.api.app import create_app
from fraud_audit_assist.config import InvestigationSettings, Settings
from fraud_audit_assist.container import Container, build_container
from fraud_audit_assist.domain.packs import load_pack
from fraud_audit_assist.domain.scenarios import load_scenarios
from tests.conftest import PACKS_DIR
from tests.integration.conftest import BACKENDS, POSTGRES_URL

INGEST_KEY = "ingest-test-key"
ANALYST_KEY = "analyst-test-key"
HEADERS = {"X-API-Key": ANALYST_KEY}


def settings_for(backend: str, tmp_path: Path, **overrides: Any) -> Settings:
    url = f"sqlite+aiosqlite:///{tmp_path / 'api.db'}" if backend == "sqlite" else POSTGRES_URL
    values: dict[str, Any] = {
        "environment": "test",
        "database_url": url,
        "pack_dir": PACKS_DIR / "investor_portal",
        "ingest_api_key": SecretStr(INGEST_KEY),
        "analyst_api_key": SecretStr(ANALYST_KEY),
        "log_json": False,
    }
    return Settings(**(values | overrides))


@asynccontextmanager
async def running(
    settings: Settings, saver: Any = None
) -> AsyncIterator[tuple[httpx.AsyncClient, Container]]:
    holder: dict[str, Container] = {}

    def factory(s: Settings) -> AbstractAsyncContextManager[Container]:
        @asynccontextmanager
        async def build() -> AsyncIterator[Container]:
            async with build_container(s, checkpointer=saver) as c:
                if not s.database_url.startswith("sqlite"):
                    await c.db.create_all()
                holder["c"] = c
                yield c

        return build()

    app = create_app(settings, factory)
    async with LifespanManager(app) as manager:
        transport = httpx.ASGITransport(app=manager.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client, holder["c"]


async def reset_postgres() -> None:
    from tests.integration.conftest import fresh_database

    db = await fresh_database("postgres", Path("."))
    await db.dispose()


@pytest.fixture(params=BACKENDS)
async def backend(request: pytest.FixtureRequest) -> str:
    if request.param == "postgres":
        await reset_postgres()
    return str(request.param)


async def post_scenarios(client: httpx.AsyncClient) -> None:
    pack = load_pack(PACKS_DIR / "investor_portal").pack
    for scenario in load_scenarios(PACKS_DIR / "investor_portal" / "scenarios", pack):
        body = {"events": [e.model_dump(mode="json") for e in scenario.events()]}
        response = await client.post("/v1/events", json=body, headers={"X-API-Key": INGEST_KEY})
        assert response.status_code == 200, response.text
        if scenario.confirmed_fraud:
            response = await client.post(
                f"/v1/accounts/{scenario.account_id}/confirmed-fraud",
                json={"decided_at": scenario.confirmed_fraud.isoformat()},
                headers=HEADERS,
            )
            assert response.status_code == 202


async def margaret_case(client: httpx.AsyncClient, container: Container) -> dict[str, Any]:
    await container.queue.join()
    cases = (await client.get("/v1/cases", params={"status": "AwaitingApproval"}, headers=HEADERS)).json()
    [case] = [c for c in cases if c["account_id"] == "ACC-MARGARET"]
    return dict((await client.get(f"/v1/cases/{case['case_id']}", headers=HEADERS)).json())


async def test_margaret_end_to_end(backend: str, tmp_path: Path) -> None:
    async with running(settings_for(backend, tmp_path), InMemorySaver()) as (client, container):
        await post_scenarios(client)
        case = await margaret_case(client, container)
        assert case["band"] == "Critical"
        assert case["finding"]["verdict"] == "AccountTakeover"
        assert case["finding"]["recommendation"] == "BlockBankChanges"
        assert all(case["finding"]["evidence"].values()), "every cited ref resolves to event ids"
        assert case["prompt_versions"] == "investigator.v1+reviewer.v1"

        decision = {
            "outcome": "ConfirmedFraud",
            "action": "BlockBankChanges",
            "analyst_id": "analyst-7",
            "notes": "Confirmed by phone.",
        }
        response = await client.post(f"/v1/cases/{case['case_id']}/decision", json=decision, headers=HEADERS)
        assert response.status_code == 200, response.text
        closed = response.json()
        assert closed["status"] == "Closed"
        assert closed["decision"]["analyst_id"] == "analyst-7"
        assert closed["interventions"][0]["action"] == "BlockBankChanges"
        assert closed["interventions"][0]["until"] is not None

        again = await client.post(f"/v1/cases/{case['case_id']}/decision", json=decision, headers=HEADERS)
        assert again.status_code == 409

        audit = (await client.get(f"/v1/cases/{case['case_id']}/audit", headers=HEADERS)).json()
        assert [a["kind"] for a in audit] == [
            "InvestigationCompleted",
            "ReviewCompleted",
            "DecisionRecorded",
            "ActionTaken",
        ]
        assert audit[0]["prompt_version"] == "investigator.v1"
        assert audit[0]["rules_version"] == "v3"
        assert audit[0]["evidence"]["cited"]


async def test_alerts_explain_every_hit(backend: str, tmp_path: Path) -> None:
    async with running(settings_for(backend, tmp_path), InMemorySaver()) as (client, _):
        await post_scenarios(client)
        [alert] = (
            await client.get("/v1/alerts", params={"account_id": "ACC-MARGARET"}, headers=HEADERS)
        ).json()
        detail = (await client.get(f"/v1/alerts/{alert['alert_id']}", headers=HEADERS)).json()
        assert [h["rule_id"] for h in detail["hits"]][:5] == ["R0", "G2", "G3", "R1", "R2"]
        assert all(h["evidence_event_ids"] for h in detail["hits"])
        assert (
            await client.get("/v1/alerts", params={"account_id": "ACC-PATEL"}, headers=HEADERS)
        ).json() == []


async def test_case_awaiting_approval_survives_a_restart(tmp_path: Path) -> None:
    settings = settings_for("sqlite", tmp_path)
    async with running(settings) as (client, container):
        await post_scenarios(client)
        case = await margaret_case(client, container)
    async with running(settings) as (client, _):
        decision = {"outcome": "FalsePositive", "action": "NoAction", "analyst_id": "analyst-2"}
        response = await client.post(f"/v1/cases/{case['case_id']}/decision", json=decision, headers=HEADERS)
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "Closed"
        assert response.json()["interventions"] == []


async def test_budget_guard_skips_investigations(tmp_path: Path) -> None:
    settings = settings_for("sqlite", tmp_path, investigation=InvestigationSettings(daily_limit=0))
    async with running(settings, InMemorySaver()) as (client, container):
        await post_scenarios(client)
        await container.queue.join()
        cases = (await client.get("/v1/cases", headers=HEADERS)).json()
        assert cases
        assert all(c["status"] == "Open" and c["not_investigated"] for c in cases)


async def test_keys_are_scoped_to_roles(tmp_path: Path) -> None:
    async with running(settings_for("sqlite", tmp_path), InMemorySaver()) as (client, _):
        assert (await client.get("/v1/cases")).status_code == 401
        assert (await client.get("/v1/cases", headers={"X-API-Key": INGEST_KEY})).status_code == 401
        assert (await client.post("/v1/events", json={"events": []}, headers=HEADERS)).status_code == 401
        assert (await client.get("/v1/cases", headers=HEADERS)).status_code == 200


async def test_bad_input_is_rejected_with_reasons(tmp_path: Path) -> None:
    async with running(settings_for("sqlite", tmp_path), InMemorySaver()) as (client, _):
        ingest = {"X-API-Key": INGEST_KEY}
        naive_time = {
            "events": [
                {
                    "event_id": "4f7d6b1e-3a6b-4c33-9c4a-2f1b1d0f0a01",
                    "account_id": "A",
                    "customer_id": "C",
                    "type": "LoginSucceeded",
                    "occurred_at": "2026-09-21T09:00:00",
                }
            ]
        }
        assert (await client.post("/v1/events", json=naive_time, headers=ingest)).status_code == 422
        unknown_type = {
            "events": [
                {**naive_time["events"][0], "occurred_at": "2026-09-21T09:00:00Z", "type": "Teleported"}
            ]
        }
        response = await client.post("/v1/events", json=unknown_type, headers=ingest)
        assert response.status_code == 422
        assert "Teleported" in response.json()["detail"]


async def test_operations_endpoints(tmp_path: Path) -> None:
    async with running(settings_for("sqlite", tmp_path), InMemorySaver()) as (client, _):
        assert (await client.get("/health/live")).json() == {"status": "ok"}
        ready = (await client.get("/health/ready")).json()
        assert ready["rules_version"] == "v3"
        response = await client.get("/metrics")
        assert "faa_events_ingested_total" in response.text
        echoed = await client.get("/health/live", headers={"X-Request-ID": "abc-123"})
        assert echoed.headers["X-Request-ID"] == "abc-123"
        rules = (await client.get("/v1/rules", headers=HEADERS)).json()
        assert rules["pack"] == "investor_portal"
