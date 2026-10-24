"""The Neighbourhood: one hop of the Link Graph around one Account, for the Investigator's graph tool (ADR-0007)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, ConfigDict

from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.graph.store import LinkGraphReader


class LinkedAccount(BaseModel):
    model_config = ConfigDict(frozen=True)

    account_id: str
    link_event_id: UUID
    confirmed_fraud: bool


class LinkedIdentifier(BaseModel):
    """One of the Account's Linking Identifiers and every other Account on it.

    An identifier nobody else uses is kept: "the new bank account is unshared" is evidence too.
    """

    model_config = ConfigDict(frozen=True)

    kind: str
    label: str
    value: str
    link_event_id: UUID
    others: list[LinkedAccount]


async def neighbourhood(account_id: str, pack: DomainPack, graph: LinkGraphReader) -> list[LinkedIdentifier]:
    kinds = [i.kind for i in pack.linking_identifiers]
    result: list[LinkedIdentifier] = []
    for mine in await graph.links_of(account_id, kinds):
        others = [
            link for link in await graph.links_on(mine.kind, mine.value) if link.account_id != account_id
        ]
        fraud = await graph.confirmed_fraud(link.account_id for link in others)
        result.append(
            LinkedIdentifier(
                kind=mine.kind,
                label=pack.identifier(mine.kind).label,
                value=mine.value,
                link_event_id=mine.first_event_id,
                others=[
                    LinkedAccount(
                        account_id=link.account_id,
                        link_event_id=link.first_event_id,
                        confirmed_fraud=link.account_id in fraud,
                    )
                    for link in others
                ],
            )
        )
    return result
