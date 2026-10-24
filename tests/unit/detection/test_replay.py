from __future__ import annotations

from fraud_audit_assist.detection.replay import OfflineReplay, replay_in_time_order
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.domain.scenarios import load_scenarios
from tests.conftest import PACKS_DIR


async def replay_investor_scenarios(pack: LoadedPack) -> OfflineReplay:
    scenarios = load_scenarios(PACKS_DIR / "investor_portal" / "scenarios", pack.pack)
    events = [e for s in scenarios for e in s.events()]
    frauds = [(s.account_id, s.confirmed_fraud) for s in scenarios if s.confirmed_fraud]
    return await replay_in_time_order(OfflineReplay(pack.pack, pack.rules), events, frauds)


async def test_margaret_reaches_high_before_any_bank_change(investor: LoadedPack) -> None:
    replay = await replay_investor_scenarios(investor)
    [alert] = replay.alerts["ACC-MARGARET"]
    by_rule = {h.rule_id: h for h in alert.hits}
    assert list(by_rule)[:5] == ["R0", "G2", "G3", "R1", "R2"]
    assert alert.band is RiskBand.CRITICAL

    crossing = alert.hit_reaching(RiskBand.HIGH, investor.rules.bands)
    timeline = {e.event_id: e for e in replay.timelines["ACC-MARGARET"]}
    assert crossing is not None
    assert timeline[crossing.evidence_event_ids[-1]].type == "MfaEnrolled"
    bank_change = next(e for e in timeline.values() if e.type == "BankDetailsChanged")
    assert crossing.triggered_at < bank_change.occurred_at


async def test_graph_evidence_cites_the_linked_accounts_events(investor: LoadedPack) -> None:
    replay = await replay_investor_scenarios(investor)
    [alert] = replay.alerts["ACC-MARGARET"]
    g2 = next(h for h in alert.hits if h.rule_id == "G2")
    assert g2.explanation == "Shares device D-77 with Confirmed Fraud Account ACC-PRIYA"
    priya_events = {e.event_id for e in replay.timelines["ACC-PRIYA"]}
    assert g2.evidence_event_ids[0] in priya_events
    g3 = next(h for h in alert.hits if h.rule_id == "G3")
    assert g3.explanation == "device D-77 is shared by 4 Accounts; Ring threshold 3"


async def test_patel_house_move_raises_nothing(investor: LoadedPack) -> None:
    replay = await replay_investor_scenarios(investor)
    assert replay.alerts["ACC-PATEL"] == []


async def test_confirmed_fraud_lifts_an_earlier_neighbour_with_a_late_hit(investor: LoadedPack) -> None:
    replay = await replay_investor_scenarios(investor)
    first = replay.alerts["ACC-DEREK"][0]
    assert [h.rule_id for h in first.hits] == ["R0", "R1", "G2"]
    assert first.band is RiskBand.HIGH
