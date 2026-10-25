from __future__ import annotations

import uuid
from typing import Any

import pytest
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import SecretStr, ValidationError

from fraud_audit_assist.agents.agents import ReplyParseError, parse_json_reply
from fraud_audit_assist.agents.evidence import EvidenceLedger, compute_baseline
from fraud_audit_assist.agents.llm.factory import build_chat_model
from fraud_audit_assist.agents.llm.fake import ScriptedChatModel
from fraud_audit_assist.agents.llm.gateway import LlmGateway, ModelEndpoint, is_retryable
from fraud_audit_assist.config import LlmSettings, Settings
from fraud_audit_assist.domain.cases import Finding
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.errors import LlmError
from fraud_audit_assist.graph.neighbourhood import LinkedAccount, LinkedIdentifier
from fraud_audit_assist.observability.metrics import Metrics
from tests.conftest import EventFactory


class StatusError(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"HTTP {status_code}")
        self.status_code = status_code


class APITimeoutError(Exception):
    pass


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (TimeoutError(), True),
        (ConnectionError(), True),
        (StatusError(429), True),
        (StatusError(503), True),
        (StatusError(400), False),
        (StatusError(401), False),
        (APITimeoutError(), True),
        (ValueError("bad"), False),
    ],
)
def test_only_transient_errors_are_retried(error: Exception, retryable: bool) -> None:
    assert is_retryable(error) is retryable


async def test_gateway_falls_back_when_the_primary_fails() -> None:
    primary = ScriptedChatModel([StatusError(401)])
    fallback = ScriptedChatModel(["fallback answer"])
    gateway = LlmGateway(
        ModelEndpoint("primary", primary),
        LlmSettings(max_retries=0),
        Metrics(),
        ModelEndpoint("fallback", fallback),
    )
    reply = await gateway.chat("investigator", "investigator.v1", [HumanMessage("hi")])
    assert reply.message.content == "fallback answer"
    assert reply.usage.model == "fallback"


async def test_gateway_records_tokens_and_cost() -> None:
    settings = LlmSettings(input_price_per_1k=1.0, output_price_per_1k=2.0)
    gateway = LlmGateway(ModelEndpoint("m", ScriptedChatModel(["x" * 400])), settings, Metrics())
    reply = await gateway.chat("reviewer", "reviewer.v1", [SystemMessage("y" * 4000)])
    assert reply.usage.input_tokens == 1000
    assert reply.usage.output_tokens == 100
    assert reply.usage.cost == pytest.approx(1.0 + 0.2)


async def test_gateway_raises_llm_error_when_every_endpoint_fails() -> None:
    gateway = LlmGateway(
        ModelEndpoint("m", ScriptedChatModel([StatusError(500), StatusError(500)])),
        LlmSettings(max_retries=1),
        Metrics(),
    )
    with pytest.raises(LlmError, match="model call failed"):
        await gateway.chat("investigator", "investigator.v1", [HumanMessage("hi")])


@pytest.mark.parametrize(
    "text",
    [
        '```json\n{"verdict": "X", "confidence": "high", "summary": "s", "recommendation": "R", "evidence": ["E-1"]}\n```'
    ],
)
def test_parse_tolerates_fences_and_enum_case(text: str) -> None:
    finding = parse_json_reply(text, Finding, {"confidence": str.capitalize})
    assert finding.confidence.value == "High"


@pytest.mark.parametrize("text", ["no json here", "{not: json}", '["a list"]', '{"verdict": "X"}'])
def test_parse_rejects_what_is_not_a_finding(text: str) -> None:
    with pytest.raises(ReplyParseError):
        parse_json_reply(text, Finding)


def test_masking_prefers_the_longest_value(investor: LoadedPack) -> None:
    ledger = EvidenceLedger()
    ledger.register("ACCOUNT", "ACC-PRI")
    ledger.register("ACCOUNT", "ACC-PRIYA")
    assert ledger.mask("ACC-PRIYA and ACC-PRI") == "ACCOUNT-2 and ACCOUNT-1"
    assert ledger.unmask("ACCOUNT-2 and ACCOUNT-1") == "ACC-PRIYA and ACC-PRI"


def test_pseudonyms_are_numbered_per_investigation(investor: LoadedPack, make_event: EventFactory) -> None:
    event = make_event("LoginSucceeded", 0, device="D-SHARED-77")
    first, second = EvidenceLedger(), EvidenceLedger()
    second.register("DEVICE", "D-OTHER-1")
    assert "DEVICE-1" in first.describe_event(event, investor.pack)
    assert "DEVICE-2" in second.describe_event(event, investor.pack)


def test_short_identifiers_are_withheld_not_leaked(investor: LoadedPack, make_event: EventFactory) -> None:
    line = EvidenceLedger().describe_event(
        make_event("LoginSucceeded", 0, account="A1", device="D1", ip="::1"), investor.pack
    )
    assert " A1 " not in line
    assert "device withheld" in line
    assert "account withheld" in line


def test_an_event_described_twice_keeps_its_ref(investor: LoadedPack, make_event: EventFactory) -> None:
    ledger = EvidenceLedger()
    event = make_event("LoginSucceeded", 0)
    assert ledger.describe_event(event, investor.pack) == ledger.describe_event(event, investor.pack)
    assert ledger.unknown_refs(["E-1", "E-2", "G-1"]) == ["E-2", "G-1"]


def test_graph_fact_marks_confirmed_fraud_and_maps_every_link(investor: LoadedPack) -> None:
    ledger = EvidenceLedger()
    mine, theirs = uuid.uuid4(), uuid.uuid4()
    identifier = LinkedIdentifier(
        kind="device",
        label="Device",
        value="D-77-LONG",
        link_event_id=mine,
        others=[LinkedAccount(account_id="ACC-PRIYA", link_event_id=theirs, confirmed_fraud=True)],
    )
    line = ledger.describe_identifier(identifier, investor.pack)
    assert line == "G-1 Device DEVICE-1 is also used by ACCOUNT-1 (Confirmed Fraud)"
    assert ledger.event_ids_by_ref["G-1"] == [str(mine), str(theirs)]


def test_baseline_counts_only_habits(make_event: EventFactory) -> None:
    events = [make_event("LoginSucceeded", m * 60 * 24) for m in range(4)] + [
        make_event("LoginSucceeded", 5000, device="D-ONCE")
    ]
    baseline = compute_baseline(events, before=None)
    assert baseline.usual_devices == ["D-HOME"]
    assert baseline.usual_countries == ["GB"]


def test_ledger_round_trips_through_json() -> None:
    ledger = EvidenceLedger()
    ledger.register("DEVICE", "D-12345")
    assert EvidenceLedger.model_validate_json(ledger.model_dump_json()) == ledger


@pytest.mark.parametrize("provider", ["openai", "azure_openai", "anthropic"])
def test_real_providers_build_without_calling_out(provider: Any) -> None:
    settings = LlmSettings(
        provider=provider, api_key=SecretStr("k"), endpoint="https://example.openai.azure.com/"
    )
    model = build_chat_model(settings, provider, "some-model", settings.api_key)
    assert model is not None


def test_settings_name_what_a_provider_is_missing() -> None:
    with pytest.raises(ValidationError, match="API key"):
        LlmSettings(provider="openai")
    with pytest.raises(ValidationError, match="endpoint"):
        LlmSettings(provider="azure_openai", api_key=SecretStr("k"))


def test_production_refuses_unsafe_settings() -> None:
    with pytest.raises(ValidationError) as error:
        Settings(environment="production", database_url="sqlite+aiosqlite:///x.db")
    message = str(error.value)
    assert "API_KEY" in message
    assert "fake" in message
    assert "SQLite" in message
