from __future__ import annotations

from datetime import timedelta

import pytest

from fraud_audit_assist.detection.detector import detect
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand, RuleSet
from tests.conftest import EventFactory

DAY = 24 * 60


def rule_ids(alert_hits: list) -> list[str]:  # type: ignore[type-arg]
    return [h.rule_id for h in alert_hits]


@pytest.fixture
def v1(investor: LoadedPack) -> RuleSet:
    from fraud_audit_assist.domain.packs import load_pack
    from tests.conftest import PACKS_DIR

    return load_pack(PACKS_DIR / "investor_portal", "v1").rules


class TestSequenceRules:
    def test_reset_then_mfa_inside_window_hits_r1(self, v1: RuleSet, make_event: EventFactory) -> None:
        events = [
            make_event("LoginSucceeded", -DAY),
            make_event("PasswordReset", 0),
            make_event("MfaEnrolled", 4),
        ]
        [alert] = detect(events, v1)
        assert rule_ids(alert.hits) == ["R1"]
        assert alert.hits[0].evidence_event_ids == [events[1].event_id, events[2].event_id]
        assert "4 min apart; window 1h" in alert.hits[0].explanation

    def test_window_is_inclusive(self, v1: RuleSet, make_event: EventFactory) -> None:
        on_boundary = detect([make_event("PasswordReset", 0), make_event("MfaEnrolled", 60)], v1)
        past_boundary = detect([make_event("PasswordReset", 0), make_event("MfaEnrolled", 60.5)], v1)
        assert rule_ids(on_boundary[0].hits) == ["R1"]
        assert past_boundary == []

    def test_out_of_order_arrival_is_scored_by_occurred_at(
        self, v1: RuleSet, make_event: EventFactory
    ) -> None:
        reset, mfa = make_event("PasswordReset", 0), make_event("MfaEnrolled", 5)
        assert detect([mfa, reset], v1) == detect([reset, mfa], v1)

    def test_duplicate_events_count_once(self, v1: RuleSet, make_event: EventFactory) -> None:
        reset, mfa = make_event("PasswordReset", 0), make_event("MfaEnrolled", 5)
        assert detect([reset, mfa, mfa, reset], v1) == detect([reset, mfa], v1)

    def test_step_b_before_step_a_does_not_match(self, v1: RuleSet, make_event: EventFactory) -> None:
        assert detect([make_event("MfaEnrolled", 0), make_event("PasswordReset", 5)], v1) == []

    def test_uses_latest_earlier_step_a(self, v1: RuleSet, make_event: EventFactory) -> None:
        old_reset = make_event("PasswordReset", 0)
        new_reset = make_event("PasswordReset", 120)
        mfa = make_event("MfaEnrolled", 130)
        [alert] = detect([old_reset, new_reset, mfa], v1)
        assert alert.hits[0].evidence_event_ids == [new_reset.event_id, mfa.event_id]


class TestMultiStepJoinAndConditions:
    def test_slow_takeover_on_one_device_hits_r7_across_pairwise_windows(
        self, investor: LoadedPack, make_event: EventFactory
    ) -> None:
        events = [
            make_event("LoginSucceeded", -7 * DAY),
            make_event("PasswordReset", 0, device="D-X", country="RO"),
            make_event("EmailChanged", 2 * DAY + 60, device="D-X", country="RO", new_email="x@example.net"),
            make_event(
                "BankDetailsChanged",
                5 * DAY + 2 * 60,
                device="D-X",
                country="RO",
                new_bank_account_hash="BA-1",
            ),
        ]
        [alert] = detect(events, investor.rules)
        assert rule_ids(alert.hits) == ["R0", "R7"]
        assert alert.band is RiskBand.HIGH

    def test_join_requires_the_same_value_on_every_step(
        self, investor: LoadedPack, make_event: EventFactory
    ) -> None:
        events = [
            make_event("PasswordReset", 0, device="D-X"),
            make_event("EmailChanged", 2 * DAY, device="D-Y", new_email="x@example.net"),
            make_event("BankDetailsChanged", 5 * DAY, device="D-X", new_bank_account_hash="BA-1"),
        ]
        hits = [h.rule_id for a in detect(events, investor.rules) for h in a.hits]
        assert "R7" not in hits

    def test_where_condition_filters_on_attribute_value(
        self, investor: LoadedPack, make_event: EventFactory
    ) -> None:
        small = [
            make_event("EmailChanged", 0, new_email="a@example.net"),
            make_event("SaleRequested", 60, amount="19999"),
        ]
        large = [
            make_event("EmailChanged", 0, new_email="a@example.net"),
            make_event("SaleRequested", 60, amount=20000),
        ]
        assert detect(small, investor.rules) == []
        assert rule_ids(detect(large, investor.rules)[0].hits) == ["R8"]

    def test_non_numeric_value_never_satisfies_a_numeric_condition(
        self, investor: LoadedPack, make_event: EventFactory
    ) -> None:
        events = [
            make_event("EmailChanged", 0, new_email="a@example.net"),
            make_event("SaleRequested", 60, amount="lots"),
        ]
        assert detect(events, investor.rules) == []

    def test_banking_payee_drain_needs_payment_to_the_new_payee(
        self, banking: LoadedPack, make_event: EventFactory
    ) -> None:
        to_new = [
            make_event("PayeeAdded", 0, payee_account_hash="PA-1"),
            make_event("TransferInitiated", 30, payee_account_hash="PA-1", amount=2500),
        ]
        to_other = [
            make_event("PayeeAdded", 0, payee_account_hash="PA-1"),
            make_event("TransferInitiated", 30, payee_account_hash="PA-OLD", amount=2500),
        ]
        assert rule_ids(detect(to_new, banking.rules)[0].hits) == ["B3"]
        assert detect(to_other, banking.rules) == []


class TestFirstSighting:
    def test_first_event_is_never_a_sighting(self, v1: RuleSet, make_event: EventFactory) -> None:
        assert detect([make_event("LoginSucceeded", 0, device="D-NEW", country="RO")], v1) == []

    def test_new_device_and_country_on_one_event_is_one_hit(
        self, v1: RuleSet, make_event: EventFactory
    ) -> None:
        [alert] = detect(
            [make_event("LoginSucceeded", 0), make_event("LoginSucceeded", 10, device="D-77", country="RO")],
            v1,
        )
        assert rule_ids(alert.hits) == ["R0"]
        assert alert.hits[0].explanation == "First sighting on this Account: new device D-77, new country RO"

    def test_known_device_is_not_a_sighting(self, v1: RuleSet, make_event: EventFactory) -> None:
        assert detect([make_event("LoginSucceeded", 0), make_event("LoginSucceeded", 10)], v1) == []


class TestVelocity:
    def test_fires_when_count_reached_inside_window(
        self, investor: LoadedPack, make_event: EventFactory
    ) -> None:
        events = [make_event("LoginFailed", m) for m in (0, 2, 4, 6, 8)]
        [alert] = detect(events, investor.rules)
        assert rule_ids(alert.hits) == ["V1"]
        assert alert.hits[0].evidence_event_ids == [e.event_id for e in events]
        assert alert.hits[0].explanation == "5 LoginFailed events within 15 min"

    def test_spread_out_failures_do_not_fire(self, investor: LoadedPack, make_event: EventFactory) -> None:
        assert detect([make_event("LoginFailed", m) for m in (0, 5, 10, 15, 20)], investor.rules) == []


class TestRiskEpisodes:
    def test_a_rule_counts_once_per_episode(self, v1: RuleSet, make_event: EventFactory) -> None:
        events = [make_event("PasswordReset", 0), make_event("MfaEnrolled", 5), make_event("MfaEnrolled", 10)]
        [alert] = detect(events, v1)
        assert rule_ids(alert.hits) == ["R1"]
        assert alert.score == 30

    def test_gap_longer_than_risk_period_opens_a_new_alert(
        self, v1: RuleSet, make_event: EventFactory
    ) -> None:
        events = [
            make_event("PasswordReset", 0),
            make_event("MfaEnrolled", 5),
            make_event("PasswordReset", 8 * DAY),
            make_event("MfaEnrolled", 8 * DAY + 5),
        ]
        alerts = detect(events, v1)
        assert [rule_ids(a.hits) for a in alerts] == [["R1"], ["R1"]]

    def test_stored_alerts_are_only_extended(self, v1: RuleSet, make_event: EventFactory) -> None:
        reset, mfa = make_event("PasswordReset", 0), make_event("MfaEnrolled", 5)
        stored = detect([reset, mfa], v1)
        email = make_event("EmailChanged", 30, new_email="x@example.net")
        [alert] = detect([reset, mfa, email], v1, stored=stored)
        assert alert.hits[: len(stored[0].hits)] == stored[0].hits
        assert rule_ids(alert.hits) == ["R1", "R2"]
        assert alert.band is RiskBand.MEDIUM

    def test_stored_alert_keeps_its_rules_version(
        self, investor: LoadedPack, v1: RuleSet, make_event: EventFactory
    ) -> None:
        reset, mfa = make_event("PasswordReset", 0), make_event("MfaEnrolled", 5)
        stored = detect([reset, mfa], v1)
        [alert] = detect([reset, mfa], investor.rules, stored=stored)
        assert alert.rules_version == "v1"

    def test_late_hit_just_before_a_stored_episode_joins_it(
        self, v1: RuleSet, make_event: EventFactory
    ) -> None:
        reset, mfa = make_event("PasswordReset", 2 * DAY), make_event("MfaEnrolled", 2 * DAY + 5)
        stored = detect([reset, mfa], v1)
        early_login = make_event("LoginSucceeded", 0)
        stranger = make_event("LoginSucceeded", DAY, device="D-X")
        alerts = detect([early_login, stranger, reset, mfa], v1, stored=stored)
        assert len(alerts) == 1
        assert rule_ids(alerts[0].hits) == ["R1", "R0"]

    def test_hit_reaching_reports_the_hit_that_crossed_the_band(
        self, v1: RuleSet, make_event: EventFactory
    ) -> None:
        events = [
            make_event("LoginSucceeded", -DAY),
            make_event("PasswordReset", 0, device="D-X"),
            make_event("MfaEnrolled", 4, device="D-X"),
            make_event("EmailChanged", 10, device="D-X", new_email="x@example.net"),
        ]
        [alert] = detect(events, v1)
        crossing = alert.hit_reaching(RiskBand.HIGH, v1.bands)
        assert crossing is not None
        assert crossing.rule_id == "R2"
        assert alert.hit_reaching(RiskBand.CRITICAL, v1.bands) is None


def test_empty_timeline_returns_stored(v1: RuleSet) -> None:
    assert detect([], v1) == []


def test_r1_window_reads_from_config(make_event: EventFactory) -> None:
    rules = RuleSet.model_validate(
        {
            "version": "t1",
            "bands": {"medium": 10, "high": 20, "critical": 30},
            "rules": [
                {
                    "id": "X",
                    "name": "x",
                    "kind": "sequence",
                    "steps": [{"types": ["A"]}, {"types": ["B"]}],
                    "window": "2h",
                    "points": 25,
                    "explanation": "{elapsed} / {window}",
                }
            ],
        }
    )
    [alert] = detect([make_event("A", 0), make_event("B", 119)], rules)
    assert alert.hits[0].explanation == "1h 59 min / 2h"
    assert alert.band is RiskBand.HIGH
    assert rules.risk_period == timedelta(days=7)


def test_risk_bands_order_by_severity_not_alphabet() -> None:
    assert RiskBand.LOW < RiskBand.MEDIUM < RiskBand.HIGH < RiskBand.CRITICAL
    assert RiskBand.CRITICAL >= RiskBand.HIGH
    assert max(RiskBand) is RiskBand.CRITICAL
