# Writing a Domain Pack

A Domain Pack turns the engine to a new business without code (ADR-0017). Copy an existing pack and edit it:

```
packs/<pack_id>/
├─ pack.yaml          what the business is
├─ rules/v1.yaml      how to score it (add v2, v3 ... for changes)
├─ personas.yaml      synthetic behaviour for Datasets
└─ scenarios/*.yaml   hand-written journeys
```

The directory name must equal `id` in `pack.yaml`. Run `uv run pytest tests/unit/domain` and
`uv run faa scenario dry-run packs/<pack_id>/scenarios --pack packs/<pack_id>`: both fail with a precise message if
anything does not line up.

## pack.yaml

| Key | Purpose |
|---|---|
| `description` | Two or three sentences on the business and the fraud. It is written into the agents' prompts. |
| `terminology` | What the agents call an Account and its owner (`Holder`, `Customer`, `Merchant`...). |
| `event_types` | Every event type the source systems may send (PascalCase). Anything else is rejected at ingestion. |
| `identifiers` | Where identifiers are read from (`device_id`, `ip_address` or `attributes.<name>`), their label, and `linking: true` when reuse across Accounts suggests one actor. Every identifier is masked before an LLM sees it. |
| `non_identifying_attributes` | Attributes shown to agents as they are (amounts, currencies). Everything else is masked. |
| `typologies` | The fraud verdicts the agents may give. `Inconclusive` and `LikelyGenuine` are always added. |
| `actions` | What an analyst may approve. Mark `restrictive: true` when it limits the customer; give `duration_days` when it expires; `records_intervention: false` for "no action". At least one must be non-restrictive. |
| `default_rules` | The rules version the service runs unless `FAA_RULES_VERSION` says otherwise. |

Choose linking identifiers with care: shared IPs and postal addresses are innocent (households, offices, carriers),
which is why both shipped packs mask them but do not link on them.

## Rules

Every rule has `id`, `name`, `kind`, `points`, an `explanation` template and optional `description` and `enabled`.

```yaml
- id: B3
  name: New payee drain
  kind: sequence
  steps:
    - types: [PayeeAdded]
    - types: [TransferInitiated]
      where:
        - { field: attributes.amount, op: gte, value: 1000 }
  window: 1d
  join_on: [attributes.payee_account_hash]   # the payment must go to the payee just added
  points: 35
  explanation: "New payee then a payment of at least 1,000 to it, {elapsed} apart; window {window}"
```

| Kind | Keys | Placeholders |
|---|---|---|
| `sequence` | `steps` (2–6, each `types` + optional `where`), `window`, optional `join_on` | `{elapsed}`, `{window}` |
| `first_sighting` | `fields` | `{sighting}` |
| `velocity` | `step`, `count`, `window` | `{count}`, `{window}`, `{types}` |
| `shared_identifier` | `identifier_kinds`, `distinct_customer` | `{identifier}`, `{value}`, `{account}` |
| `fraud_proximity` | `identifier_kinds` | `{identifier}`, `{value}`, `{account}` |
| `ring` | `identifier_kinds`, `account_threshold`, `max_hops` | `{identifier}`, `{value}`, `{accounts}`, `{threshold}`, `{hops}` |

Conditions: `eq`, `ne`, `in`, `not_in` (list value), `gt`, `gte`, `lt`, `lte`, `exists`. Fields are core fields
(`type`, `channel`, `country`, `device_id`, `ip_address`, `account_id`, `customer_id`) or `attributes.<name>`.
Durations: `30m`, `4h`, `1d12h`.

Never edit a rules file that has scored Alerts: copy it to the next version.

## Personas

See the header of `src/fraud_audit_assist/simulator/personas.py` for placeholders. Each Persona has a `label` (a
typology or `Genuine`), a `share` of all Accounts and a `min_count`; exactly one has `fill: true`. Use `vars` for a
random value that several steps must share, and `from: <step number>` to time a step from an earlier one.

## Scenarios

The same format as `packs/investor_portal/scenarios/margaret-ato.yaml`: a `baseline` (the usual login, repeated
weekly), named `actors`, and `steps` with offsets from `start`. Add `label` for ground truth and `confirmed_fraud` to
seed a known fraud after the journey.
