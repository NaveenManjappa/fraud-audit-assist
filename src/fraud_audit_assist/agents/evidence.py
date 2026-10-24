"""The Evidence Ledger: one Investigation's Pseudonyms and Evidence Refs, and the only way evidence reaches an LLM.

Every line shown to a model is written here: each identifier in it is registered and replaced by a per-Investigation
Pseudonym (`ACCOUNT-1`, `DEVICE-2`), each event gets an `E-n` ref and each graph fact a `G-n` ref (ADR-0008).

The ledger is a plain serialisable model, so it lives in the LangGraph state and is checkpointed with it: an
Investigation can resume at any step, not only at Await Approval.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

from fraud_audit_assist.domain.events import Event
from fraud_audit_assist.domain.packs import DomainPack
from fraud_audit_assist.graph.neighbourhood import LinkedIdentifier

# A registered value is replaced wherever it appears, so one this short would also be cut out of timestamps and refs;
# it is withheld instead.
MIN_MASKED_LENGTH = 4

# Shown values are untrusted text from source systems. Only short single tokens pass (amounts, currencies, channel
# names), so an attribute cannot carry a sentence of instructions into a prompt.
_SAFE_PLAIN_VALUE = re.compile(r"^[\w.,:/+-]{1,32}$")


# Core identifier fields are always masked, even in a pack that does not list them as identifiers.
_CORE_PREFIXES = {"device_id": "DEVICE", "ip_address": "IP_ADDRESS"}


class CustomerBaseline(BaseModel):
    usual_countries: list[str]
    usual_devices: list[str]
    usual_channels: list[str]
    events_per_week: float
    history_days: int
    event_types: dict[str, int]


class EvidenceLedger(BaseModel):
    pseudonym_by_value: dict[str, str] = Field(default_factory=dict)
    counters: dict[str, int] = Field(default_factory=dict)
    event_ids_by_ref: dict[str, list[str]] = Field(default_factory=dict)
    ref_by_event: dict[str, str] = Field(default_factory=dict)
    line_by_ref: dict[str, str] = Field(default_factory=dict)
    next_event_ref: int = 1
    next_graph_ref: int = 1

    @property
    def pseudonym_map(self) -> dict[str, str]:
        """Pseudonym to real value: stored with the Case so the analyst's view can be unmasked."""
        return {pseudonym: value for value, pseudonym in self.pseudonym_by_value.items()}

    def register(self, prefix: str, value: str) -> str | None:
        """The value's Pseudonym, assigning the next one for its prefix the first time. None when withheld."""
        value = value.strip()
        if len(value) < MIN_MASKED_LENGTH:
            return None
        if value in self.pseudonym_by_value:
            return self.pseudonym_by_value[value]
        number = self.counters.get(prefix, 0) + 1
        self.counters[prefix] = number
        pseudonym = f"{prefix}-{number}"
        self.pseudonym_by_value[value] = pseudonym
        return pseudonym

    def mask(self, text: str) -> str:
        return _replace_all(text, self.pseudonym_by_value)

    def unmask(self, text: str) -> str:
        return _replace_all(text, self.pseudonym_map)

    def describe_event(self, event: Event, pack: DomainPack) -> str:
        """One masked line, e.g. `E-3 2026-09-22T09:04:00Z BankDetailsChanged account ACCOUNT-1 ...`."""
        key = str(event.event_id)
        if key in self.ref_by_event:
            return self.line_by_ref[self.ref_by_event[key]]

        account = self.register("ACCOUNT", event.account_id) or "withheld"
        customer = self.register("CUSTOMER", event.customer_id) or "withheld"
        device = self._core_identifier("device_id", event.device_id, pack)
        ip = self._core_identifier("ip_address", event.ip_address, pack)
        attributes = "".join(
            f" {name}={self._attribute(name, value, pack)}"
            for name, value in sorted(event.attributes.items())
        )
        ref = f"E-{self.next_event_ref}"
        self.next_event_ref += 1
        self.ref_by_event[key] = ref
        self.event_ids_by_ref[ref] = [key]
        # Identifiers are written as their Pseudonyms here, not left for mask(): a value too short to register would
        # otherwise reach the model raw.
        line = (
            f"{ref} {_timestamp(event.occurred_at)} {event.type} account {account} customer {customer} "
            f"device {device} ip {ip} country {event.country or 'unknown'} channel {_plain(event.channel)}{attributes}"
        )
        return self._shown(ref, line)

    def describe_identifier(self, identifier: LinkedIdentifier, pack: DomainPack) -> str:
        """One graph fact, e.g. `G-2 Device DEVICE-1 is also used by ACCOUNT-2, ACCOUNT-3 (Confirmed Fraud)`."""
        value = self.register(pack.identifier(identifier.kind).pseudonym_prefix, identifier.value)
        if value is None:
            # An identifier too short to mask could reach the model raw, so it is never described.
            raise ValueError(f"identifier of kind {identifier.kind} is too short to mask safely")
        others = [
            (self.register("ACCOUNT", o.account_id) or "withheld")
            + (" (Confirmed Fraud)" if o.confirmed_fraud else "")
            for o in identifier.others
        ]
        ref = f"G-{self.next_graph_ref}"
        self.next_graph_ref += 1
        # The ref stands for every link in the fact, this Account's first, so the analyst can check each one.
        self.event_ids_by_ref[ref] = [
            str(identifier.link_event_id),
            *(str(o.link_event_id) for o in identifier.others),
        ]
        if others:
            line = f"{ref} {identifier.label} {value} is also used by {', '.join(others)}"
        else:
            line = f"{ref} {identifier.label} {value} is used by no other {pack.terminology.account}"
        return self._shown(ref, line)

    def describe_baseline(self, baseline: CustomerBaseline, pack: DomainPack) -> str:
        device_prefix = next(
            (i.pseudonym_prefix for i in pack.identifiers if i.field == "device_id"), "DEVICE"
        )
        devices = [self.register(device_prefix, d) or "withheld" for d in baseline.usual_devices]
        types = ", ".join(f"{name} x{count}" for name, count in sorted(baseline.event_types.items()))
        return self.mask(
            f"History: {baseline.history_days} days. Usual countries: {_list(baseline.usual_countries)}. "
            f"Usual devices: {_list(devices)}. Usual channels: {_list(baseline.usual_channels)}. "
            f"Events per week: {baseline.events_per_week:.1f}. Event types: {types or 'none'}."
        )

    def unknown_refs(self, refs: Iterable[str]) -> list[str]:
        return sorted({r for r in refs if r not in self.event_ids_by_ref})

    def lines_for(self, refs: Iterable[str]) -> list[str]:
        """The masked line each ref was shown with, so the Reviewer sees exactly what the Investigator saw."""
        return [self.line_by_ref[r] for r in dict.fromkeys(refs) if r in self.line_by_ref]

    def all_lines(self) -> list[str]:
        return list(self.line_by_ref.values())

    def event_ids_for(self, refs: Iterable[str]) -> dict[str, list[str]]:
        return {r: self.event_ids_by_ref[r] for r in dict.fromkeys(refs) if r in self.event_ids_by_ref}

    def _core_identifier(self, field: str, value: str | None, pack: DomainPack) -> str:
        if not value:
            return "none"
        spec = next((i for i in pack.identifiers if i.field == field), None)
        prefix = spec.pseudonym_prefix if spec else _CORE_PREFIXES[field]
        return self.register(prefix, value) or "withheld"

    def _attribute(self, name: str, value: object, pack: DomainPack) -> str:
        text = str(value)
        if name in pack.non_identifying_attributes:
            return text if _SAFE_PLAIN_VALUE.match(text) else "withheld"
        spec = next((i for i in pack.identifiers if i.field == f"attributes.{name}"), None)
        # Masked by default: an attribute a source adds later must not reach the model raw.
        return self.register(spec.pseudonym_prefix if spec else "VALUE", text) or "withheld"

    def _shown(self, ref: str, line: str) -> str:
        masked = self.mask(line)
        self.line_by_ref[ref] = masked
        return masked


def compute_baseline(events: Iterable[Event], before: datetime | None) -> CustomerBaseline:
    """The customer's usual behaviour from events before `before`: what flagged activity is contrasted with."""
    history = [e for e in events if before is None or e.occurred_at < before]
    if not history:
        return CustomerBaseline(
            usual_countries=[],
            usual_devices=[],
            usual_channels=[],
            events_per_week=0,
            history_days=0,
            event_types={},
        )
    span_days = max(1, (history[-1].occurred_at - history[0].occurred_at).days)
    types: dict[str, int] = {}
    for e in history:
        types[e.type] = types.get(e.type, 0) + 1
    return CustomerBaseline(
        usual_countries=_usual(e.country for e in history),
        usual_devices=_usual(e.device_id for e in history),
        usual_channels=_usual(e.channel for e in history),
        events_per_week=len(history) / span_days * 7,
        history_days=span_days,
        event_types=types,
    )


def _usual(values: Iterable[str | None], top: int = 3) -> list[str]:
    counts: dict[str, int] = {}
    for v in values:
        if v:
            counts[v] = counts.get(v, 0) + 1
    # Seen at least twice: one sighting is not a habit.
    usual = [v for v, c in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])) if c >= 2]
    return usual[:top]


def _replace_all(text: str, replacements: dict[str, str]) -> str:
    # One regex pass with the longest keys first: at each position the longest match wins, so ACC-PRIYA is never
    # masked as ACC-PRI + "YA", and a replacement is never scanned again.
    if not replacements:
        return text
    pattern = "|".join(re.escape(k) for k in sorted(replacements, key=len, reverse=True))
    return re.sub(pattern, lambda m: replacements[m.group(0)], text)


def _timestamp(value: datetime) -> str:
    return value.strftime("%Y-%m-%dT%H:%M:%SZ")


def _plain(value: str) -> str:
    return value if _SAFE_PLAIN_VALUE.match(value) else "withheld"


def _list(values: list[str]) -> str:
    return ", ".join(values) if values else "none"


def event_ids(values: Iterable[str]) -> list[UUID]:
    return [UUID(v) for v in values]
