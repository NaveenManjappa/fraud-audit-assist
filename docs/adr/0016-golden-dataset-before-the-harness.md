# ADR-0016: A golden dataset comes before the evaluation harness

Status: Accepted | Date: 2026-10-24

`evals/golden/` holds 26 hand-curated cases across both packs, each testing one idea, with expected detection
(exact, computed by replay and reviewed) and expected investigation (ranges of acceptable verdicts and
recommendations, required citations, forbidden phrases). Known misses are kept on purpose.

The harness that scores against it (detection metrics, agent grounding and agreement, an LLM judge, CI thresholds)
is deliberately not built yet. Fixing the cases and their expected answers first means the harness is written
against an agreed definition of "correct", rather than the definition drifting to fit the first results.
