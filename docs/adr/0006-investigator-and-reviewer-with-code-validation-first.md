# ADR-0006: Two agents, with code validation before review

Status: Accepted | Date: 2026-10-24

The Investigator (`investigator.v1`) calls its tools and writes a Finding: verdict, confidence, summary (at most 120
words), recommendation and the Evidence Refs it cites. The Reviewer (`reviewer.v1`) has no tools; it sees the
Finding and, for each cited ref, exactly the masked line the Investigator was shown, and answers
`{approved, reasons[]}`.

Before the Reviewer runs, code checks what code can check for certain and for free:
- the verdict and recommendation are values the pack defines;
- the summary is present and within the word limit;
- at least one ref is cited, and every cited ref was issued in this Investigation;
- **guardrail:** a restrictive recommendation (one that limits the customer, such as blocking payouts) needs a fraud
  verdict. An agent that calls a customer genuine, or cannot judge, may not recommend restricting them.

A failure is a rejection with reasons, and costs no Reviewer call. One retry follows any rejection, with the reasons
appended. A Finding rejected twice reaches the analyst with a Review Flag, unless it is incomplete, which fails the
Investigation. An unreadable Investigator reply gets the retry; a second one fails. An LLM error, or an unreadable
review, fails the Investigation: an unchecked Finding is never shown as if it were reviewed.

Replies are parsed by code into pydantic models (schema-validated output) rather than trusted as structured
output, so the same validation applies to every provider and to the fake.

## Considered options

- **One agent that checks itself**: half the calls, but a model reviewing its own Finding tends to agree with it.
- **More retries**: more Findings rescued, but each retry is another full tool loop of tokens.
