"""Versioned prompts. The file name is the Prompt Version, recorded with every Case and Audit Entry."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from importlib import resources
from string import Template

from fraud_audit_assist.domain.packs import DomainPack

MAX_SUMMARY_WORDS = 120


@dataclass(frozen=True)
class Prompt:
    version: str
    text: str

    @property
    def digest(self) -> str:
        """A short content hash, so an audit can tell two edits of the same version apart."""
        return hashlib.sha256(self.text.encode()).hexdigest()[:12]


def load_prompt(version: str, pack: DomainPack) -> Prompt:
    """Loads `<version>.md` and fills in the pack's language. Unknown placeholders fail, never pass through."""
    raw = resources.files(__package__).joinpath(f"{version}.md").read_text(encoding="utf-8")
    account = pack.terminology.account
    values = {
        "pack_name": pack.name,
        "pack_description": pack.description,
        "account_term": account,
        "account_term_plural": f"{account}s",
        "customer_term": pack.terminology.customer,
        "verdicts": ", ".join(pack.verdicts),
        "verdict_list": "\n".join(
            [f"- `{t.id}`: {t.description}" for t in pack.typologies]
            + [
                "- `Inconclusive`: the evidence does not support any verdict with confidence.",
                f"- `LikelyGenuine`: the {pack.terminology.customer} appears to be acting legitimately.",
            ]
        ),
        "actions": ", ".join(f"{a.id} (restrictive)" if a.restrictive else a.id for a in pack.actions),
        "action_list": "\n".join(
            f"- `{a.id}`{' (restrictive)' if a.restrictive else ''}: {a.description}" for a in pack.actions
        ),
        "max_words": str(MAX_SUMMARY_WORDS),
    }
    return Prompt(version=version, text=Template(raw).substitute(values))
