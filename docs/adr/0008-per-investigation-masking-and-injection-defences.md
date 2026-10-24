# ADR-0008: Identifiers are masked per Investigation; attributes are masked by default

Status: Accepted | Date: 2026-10-24

No identifier reaches an LLM. Each Investigation has its own Evidence Ledger, which swaps every identifier for a
Pseudonym numbered per Investigation (`ACCOUNT-1`, `DEVICE-2`, `BANK_ACCOUNT-1`), so prompts cannot be correlated
across Cases. Event types, times, countries and allow-listed values are kept, because the agents reason on them.

- **By value, longest first.** Rule Hit explanations are rendered text ("Shares device D-77 with ACC-PRIYA"), so
  registered values are replaced wherever they appear, in one regex pass with the longest values first.
- **Short values are withheld.** A value under four characters would be cut out of timestamps if masked by value, so
  it is shown as `withheld`; core identifiers are always written as their Pseudonyms, never left to text masking.
- **Attributes are masked by default.** Only a pack's `non_identifying_attributes` are shown, and only when they are
  a short single token. Any other attribute, including a field a source adds later, becomes `VALUE-n`.
- **The map stays server-side** on the Case; only the analyst's view is unmasked.

These rules are also the prompt-injection defence: source data is untrusted text, and free text never reaches a
model. The prompts also tell the agents that evidence is data, never instructions.
