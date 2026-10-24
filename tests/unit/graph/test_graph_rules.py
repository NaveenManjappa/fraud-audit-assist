from __future__ import annotations

from fraud_audit_assist.detection.replay import OfflineReplay, replay_in_time_order
from fraud_audit_assist.domain.packs import LoadedPack
from fraud_audit_assist.domain.rules import RiskBand
from fraud_audit_assist.graph.links import links_from
from fraud_audit_assist.graph.rules import graph_hits, neighbours
from fraud_audit_assist.graph.store import InMemoryLinkGraph
from tests.conftest import EventFactory

DAY = 24 * 60


def test_links_keep_the_earliest_use(investor: LoadedPack, make_event: EventFactory) -> None:
    later = make_event("LoginSucceeded", 10, device="D-1")
    earlier = make_event("LoginSucceeded", 0, device="D-1")
    [link] = links_from([later, earlier], investor.pack)
    assert link.first_event_id == earlier.event_id


def test_ip_addresses_never_link(investor: LoadedPack, make_event: EventFactory) -> None:
    links = links_from([make_event("LoginSucceeded", 0, device=None, ip="198.51.100.1")], investor.pack)
    assert links == []


async def test_shared_bank_account_needs_a_different_customer(
    investor: LoadedPack, make_event: EventFactory
) -> None:
    graph = InMemoryLinkGraph()
    graph.add(
        links_from(
            [
                make_event("BankDetailsChanged", 0, account="A", customer="C1", new_bank_account_hash="BA-1"),
                make_event("BankDetailsChanged", 5, account="B", customer="C1", new_bank_account_hash="BA-1"),
                make_event("BankDetailsChanged", 9, account="C", customer="C2", new_bank_account_hash="BA-1"),
            ],
            investor.pack,
        )
    )
    hits = [h for h in await graph_hits("A", investor.rules, investor.pack, graph) if h.rule_id == "G1"]
    assert [h.explanation for h in hits] == [
        "Pays out to bank account BA-1, which C of a different Holder also pays out to"
    ]
    # C's link formed second, so it completes the pair and triggers the hit.
    assert hits[0].triggered_at == make_event("X", 9).occurred_at


async def test_one_hop_ring_lifts_earlier_members_at_the_third_link(
    investor: LoadedPack, make_event: EventFactory
) -> None:
    events = [
        make_event("LoginSucceeded", m, account=a, device="D-RING")
        for m, a in ((0, "A"), (10, "B"), (20, "C"))
    ]
    graph = InMemoryLinkGraph()
    graph.add(links_from(events, investor.pack))
    [ring] = [h for h in await graph_hits("A", investor.rules, investor.pack, graph) if h.rule_id == "G3"]
    assert ring.evidence_event_ids == [e.event_id for e in events]
    assert ring.triggered_at == events[2].occurred_at


async def test_two_hop_ring_catches_a_chain_one_hop_misses(
    banking: LoadedPack, make_event: EventFactory
) -> None:
    # A and B share a device, B and C share a phone: no single identifier has three Accounts.
    events = [
        make_event("LoginSucceeded", 0, account="A", device="D-1"),
        make_event("LoginSucceeded", 10, account="B", device="D-1"),
        make_event("PhoneChanged", 20, account="B", device="D-B", new_phone="+44 7700 900001"),
        make_event("PhoneChanged", 30, account="C", device="D-C", new_phone="+44 7700 900001"),
    ]
    graph = InMemoryLinkGraph()
    graph.add(links_from(events, banking.pack))
    [ring] = [h for h in await graph_hits("A", banking.rules, banking.pack, graph) if h.rule_id == "G3"]
    assert ring.triggered_at == events[3].occurred_at
    assert ring.evidence_event_ids[-1] == events[3].event_id
    assert "3 Accounts within 2 hop(s)" in ring.explanation


async def test_neighbours_reach_as_far_as_the_widest_ring(
    banking: LoadedPack, make_event: EventFactory
) -> None:
    graph = InMemoryLinkGraph()
    old = [
        make_event("LoginSucceeded", 0, account="B", device="D-1"),
        make_event("PhoneChanged", 5, account="B", device="D-B", new_phone="+44 7700 900002"),
        make_event("PhoneChanged", 6, account="C", device="D-C", new_phone="+44 7700 900002"),
    ]
    graph.add(links_from(old, banking.pack))
    new = [make_event("LoginSucceeded", 10, account="A", device="D-1")]
    new_links = links_from(new, banking.pack)
    graph.add(new_links)
    kinds = [i.kind for i in banking.pack.linking_identifiers]
    assert await neighbours(new_links, ["A"], graph, kinds, hops=1) == ["B"]
    assert await neighbours(new_links, ["A"], graph, kinds, hops=2) == ["B", "C"]


async def test_banking_mule_payee_shared_by_victims_reaches_high(
    banking: LoadedPack, make_event: EventFactory
) -> None:
    def drain(account: str, start: int) -> list:  # type: ignore[type-arg]
        return [
            make_event("LoginSucceeded", start - 7 * DAY, account=account),
            make_event(
                "PayeeAdded", start, account=account, device=f"D-X-{account}", payee_account_hash="PA-MULE"
            ),
            make_event(
                "TransferInitiated",
                start + 20,
                account=account,
                device=f"D-X-{account}",
                payee_account_hash="PA-MULE",
                amount=4000,
            ),
        ]

    replay = await replay_in_time_order(
        OfflineReplay(banking.pack, banking.rules), drain("V1", 0) + drain("V2", 60)
    )
    [alert] = replay.alerts["V1"]
    assert {h.rule_id for h in alert.hits} == {"B0", "B3", "G1"}
    assert alert.band is RiskBand.HIGH
