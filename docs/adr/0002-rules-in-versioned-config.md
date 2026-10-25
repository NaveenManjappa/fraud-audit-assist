# ADR-0002: Rules live in versioned YAML, per Domain Pack

Status: Accepted | Date: 2026-10-24

Rules, Windows, points, band thresholds and the risk period live in `packs/<pack>/rules/vN.yaml`, not in Python. An
analyst can tune a Window or a weight without a code change, comments in YAML can say why a rule exists, and the
file's `version` is stamped on every Alert and on every Rule Hit so an auditor can see which rules produced it.

The service loads one Rule Set at startup (the pack's `default_rules` unless `FAA_RULES_VERSION` names another) and
fails fast on anything malformed: an unknown placeholder in an explanation, a non-positive Window, a sequence with
fewer than two steps, an event type or identifier the pack does not define. `GET /v1/rules` shows what is live.

Changing a rule means a new file version; stored Rule Hits are never rescored under a newer version.

## Considered options

- **JSON**: equivalent for machines, but YAML allows the comments analysts need beside a weight.
- **Rules in the database**: editable at runtime, but needs an admin UI and its own audit trail.

## Rule Hits record their own version

Each Rule Hit also records the Rule Set version that produced it, because a stored Alert can be extended by hits
from a newer Rule Set than the one that opened it.
