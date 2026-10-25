# ADR-0001: Rules first, AI second

Status: Accepted | Date: 2026-10-24

Every event is scored by deterministic Rules; AI agents only investigate Accounts the Rules have already made risky
(a Case, opened at High). Rules are explainable (each Rule Hit names the events behind it), auditable, fast enough to
run inside the ingest request and free, so they can watch every Account. An LLM on every event would be slow, costly
and impossible to explain to an auditor.

Detection is one pure function, `detect(timeline, rules, stored, graph_hits)`, with no database and no clock. Live
ingestion, dry runs and offline replay run exactly the same code.

## Considered options

- **LLM scores every event**: flexible, but non-deterministic, expensive at volume and hard to defend in a regulated setting.
- **Anomaly model first**: needs labelled history that does not exist yet; it is the next step once Analyst Decisions accumulate.
