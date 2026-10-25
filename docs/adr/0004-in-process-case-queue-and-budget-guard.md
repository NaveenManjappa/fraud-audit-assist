# ADR-0004: Investigations run from an in-process queue, behind a budget guard

Status: Accepted | Date: 2026-10-24

When ingestion opens a Case, it puts the CaseId on a `CaseQueue` after the transaction commits, so a rolled-back batch
never starts an Investigation. The queue is a bounded `asyncio.Queue` (a full queue makes ingestion wait rather than
grow memory) behind a protocol, which is the seam for a durable broker later.

The database, not the queue, is the durable record. On startup the worker re-queues every Case left Open or
Investigating, except those the budget guard skipped on purpose, and releases any Case a crash left mid-decision.

The budget guard sits in the worker: `FAA_INVESTIGATION__ENABLED` switches Investigations off and
`FAA_INVESTIGATION__DAILY_LIMIT` caps those started per UTC day, counted from `investigation_started_at` so the count
survives a restart. A Case over the limit stays Open, marked `not_investigated`, and is never re-queued. The worker
takes one Case at a time, which keeps the count free of races; the design assumes one API instance.

## Considered options

- **Durable broker now (a queue service, or a Postgres outbox)**: survives crashes without a startup scan and scales
  out, but adds infrastructure a single-instance service does not need yet.
- **Investigate inside the ingest request**: no queue, but an LLM call would hold the ingest transaction open.
