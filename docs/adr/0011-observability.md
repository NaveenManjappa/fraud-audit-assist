# ADR-0011: Structured logs, OpenTelemetry traces and Prometheus metrics

Status: Accepted | Date: 2026-10-24

- **Logs**: structlog JSON to stdout, one line per event with the request id (from `X-Request-ID` or generated) bound
  to every line logged while serving that request. Secrets are never logged; a redaction step is the backstop.
- **Traces**: OpenTelemetry spans for each request (FastAPI instrumentation) and for `ingest`, `detect`,
  `investigate case`, `chat <agent>` and `decide case`. Exported only when `FAA_OTEL_EXPORTER_OTLP_ENDPOINT` is set.
- **Metrics**: `/metrics` in Prometheus format: events ingested, rule hits by rule, band changes, Cases opened,
  ingest latency, LLM calls, tokens and cost by agent, Investigations by outcome and duration.
- **Health**: `/health/live` (process up) and `/health/ready` (database reachable).

Fraud Alerts are a product feature stored in the database; these are operational signals for engineers.
