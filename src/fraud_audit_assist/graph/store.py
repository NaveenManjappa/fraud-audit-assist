"""The Link Graph's read interface, and an in-memory implementation for dry runs, replay and tests."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Protocol

from fraud_audit_assist.graph.links import Link


class LinkGraphReader(Protocol):
    """What graph rules, Neighbour discovery and the Investigator's graph tool read.

    Both implementations answer the same questions, so the rules are written once (ADR-0014).
    """

    async def links_of(self, account_id: str, kinds: Iterable[str]) -> list[Link]:
        """The Account's own links of the given kinds."""
        ...

    async def links_on(self, kind: str, value: str) -> list[Link]:
        """Every Account's link to one identifier, this Account's included."""
        ...

    async def confirmed_fraud(self, account_ids: Iterable[str]) -> set[str]:
        """Which of the given Accounts are Confirmed Fraud."""
        ...


class InMemoryLinkGraph:
    """A Link Graph held in dictionaries. Same answers as the SQL one, used where no database is wanted."""

    def __init__(self) -> None:
        self._by_identifier: dict[tuple[str, str], dict[str, Link]] = defaultdict(dict)
        self._by_account: dict[str, dict[tuple[str, str], Link]] = defaultdict(dict)
        self._fraud: set[str] = set()

    def add(self, links: Iterable[Link]) -> None:
        for link in links:
            existing = self._by_account[link.account_id].get(link.identifier)
            kept = link.earlier_of(existing) if existing else link
            self._by_account[link.account_id][link.identifier] = kept
            self._by_identifier[link.identifier][link.account_id] = kept

    def mark_confirmed_fraud(self, account_id: str) -> None:
        self._fraud.add(account_id)

    async def links_of(self, account_id: str, kinds: Iterable[str]) -> list[Link]:
        wanted = set(kinds)
        links = [link for link in self._by_account.get(account_id, {}).values() if link.kind in wanted]
        return sorted(links, key=lambda link: (link.kind, link.value))

    async def links_on(self, kind: str, value: str) -> list[Link]:
        links = self._by_identifier.get((kind, value), {}).values()
        return sorted(links, key=lambda link: (*link.sort_key(), link.account_id))

    async def confirmed_fraud(self, account_ids: Iterable[str]) -> set[str]:
        return self._fraud & set(account_ids)
