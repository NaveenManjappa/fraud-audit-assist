# Golden dataset (v1)

Hand-curated, labelled cases for evaluating detection and the investigation agents. One JSON object per line, one
file per Domain Pack:

| File | Cases | Covers |
|---|---|---|
| `investor_portal.v1.jsonl` | 16 | Takeover, registration fraud, rings, slow-burn evasion, channel abuse, prompt injection, genuine false positives and near-misses, known blind spots |
| `digital_banking.v1.jsonl` | 10 | SIM-swap takeover, mule payees, application fraud, 2-hop chain rings, remote-access scams, genuine near-misses, known misses |

This directory holds **data only**. The harness that scores against it is not built yet; the format is meant to be
read by that harness, and by a person reviewing a case.

## Why it exists

The simulator's Datasets are large and random, which is right for rates (detection, false positives) but wrong for
regression and for judging agents: a reviewer cannot read a thousand Accounts. Golden cases are few, fixed and
written so that each one tests one idea, with the expected outcome stated in advance.

## One case

```json
{
  "id": "ip-007",
  "pack": "investor_portal",
  "rules_version": "v3",
  "title": "Travelling holder: genuine reset, MFA and phone change abroad",
  "description": "The false positive the feedback loop learns from: the agents should not recommend blocking.",
  "tags": ["genuine", "false-positive"],
  "context": [],
  "target": { "...": "a Scenario, exactly as in packs/<pack>/scenarios" },
  "label": { "kind": "Genuine" },
  "expected": {
    "detection": {
      "flagged": true,
      "peak_band": "High",
      "peak_score": 60,
      "first_high_step": 3,
      "flagged_before_event_type": null,
      "rules_hit": ["R0", "R1", "R2"]
    },
    "investigation": {
      "case_expected": true,
      "acceptable_verdicts": ["LikelyGenuine", "Inconclusive"],
      "acceptable_recommendations": ["ContactHolder", "NoAction"],
      "min_confidence": null,
      "must_cite_event_types": ["PasswordReset"],
      "must_cite_graph": false,
      "must_not_contain": [],
      "notes": "Reaches High but there is no payout change, no sale and no shared identifier."
    }
  }
}
```

### Fields

| Field | Meaning |
|---|---|
| `id` | Stable id; never reused. `ip-` investor portal, `db-` digital banking. |
| `rules_version` | The Rule Set the detection expectation was computed with. A case for an older version (such as `ip-005` on v2) documents a gap a later version closed. |
| `tags` | Free labels for slicing results: `ato`, `registration`, `application`, `ring`, `graph`, `multi-hop`, `evasion`, `genuine`, `false-positive`, `near-miss`, `known-miss`, `blind-spot`, `adversarial`, `early-detection`, `late-detection`, `channel`, `mule`, `remote-access`. |
| `context` | Scenarios replayed before the target, in first-event order, with any `confirmed_fraud` applied after its Scenario. They give the graph its history (D-77's earlier Accounts, a ring's first members, a mule's earlier victim). |
| `target` | The Account being judged, as a Scenario. Its events are replayed one step at a time. |
| `label` | Ground truth: a typology of the pack or `Genuine`, with optional `tags` (`evasion`, `ring`, `blind-spot`, ...). Never sent to the API. |
| `expected.detection` | What the rules must produce for the target. `first_high_step` is the 1-based step at which the Account first reached High (null if never). `flagged_before_event_type` names the money-moving step that came after the first High, when there was one: the early-detection goal. |
| `expected.investigation` | What an acceptable Finding looks like, or `null` where no Investigation would run and none is worth sampling. `case_expected` says whether live detection would open a Case; `false` marks a Near-miss worth sampling to test that the agents can clear it. A Finding passes when its verdict and recommendation are in the acceptable lists, its confidence is at least `min_confidence` (null means any), it cites at least one event of each type in `must_cite_event_types`, it cites a `G-n` graph fact when `must_cite_graph` is true, and its unmasked summary contains none of `must_not_contain` (case-insensitive). |

### Reading the expectations

- **Detection expectations are exact.** They were computed by replaying each case through the rules engine at the
  stated `rules_version`, then reviewed by hand against the case's intent. A change to a rule that alters one of
  them is a behaviour change to be justified, not a test to update blindly.
- **Investigation expectations are ranges.** Several verdicts or recommendations can be defensible; the lists hold
  every answer a careful analyst would accept. `notes` says what a good Finding should notice.
- **Known misses are kept on purpose** (`known-miss`, `blind-spot`): credential stuffing without a reset (`ip-012`),
  MFA removal (`ip-016`), an application payout under the threshold (`db-005`) and password guessing before money
  moves (`db-009`). They measure what the rules cannot yet see.

## Changing the dataset

- Add a case rather than edit one; give it the next id. Editing a case's expectation changes what "correct" means for
  every past result.
- When a new Rule Set changes a detection expectation on purpose, copy the file to `v2`, change the expectation there
  and say why in the case's `description`.
- Use documentation IP ranges (192.0.2.x, 198.51.100.x, 203.0.113.x, 2001:db8::/32), invented device ids and
  `example.net` / `example.org` emails. Never real personal data.
