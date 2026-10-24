# ADR-0018: Generic rule kinds: joined multi-step sequences, conditions and velocity

Status: Accepted | Date: 2026-10-24

vigil's sequence rules had exactly two steps. A generic engine needs more without code:

- **Multi-step sequences** (2 to 6 steps) inside one Window. For each event completing the last step, the latest
  matching earlier event is taken for each prior step, walking backwards; that gives the shortest span, so if any
  combination fits the Window this one does.
- **`join_on`**: every step must share a field value, such as the same device (`device_id`) or payee
  (`attributes.payee_account_hash`). A missing value never joins.
- **`where` conditions** on any field: `eq`, `ne`, `in`, `not_in`, `gt`, `gte`, `lt`, `lte`, `exists`. Numeric
  comparisons fail closed on a non-numeric value.
- **`velocity`**: at least N matching events inside a Window (password guessing, payee sprees).
- **`first_sighting`** on any configured fields, not only device and country.

Investor portal v3 uses these to close vigil's best-known gap: the slow-burn fraudster spaces each step beyond its
pairwise Window, but one new device doing reset, contact change and bank change within 14 days (R7) is still one
takeover.
