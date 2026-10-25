# Runbook

How to run, operate and troubleshoot the service. Commands run from the repository root. For a step-by-step
walkthrough with real outputs, read the [user guide](user-guide.md).

## Start locally

```bash
uv sync                                   # Python 3.12+, installs from uv.lock
uv run faa serve                          # SQLite + fake LLM; schema created on start in development
```

Or with PostgreSQL in Docker:

```bash
docker compose up --build                 # migrates, then serves on http://localhost:8000
```

Check it: `curl localhost:8000/health/ready` → `{"status":"ok","pack":"investor_portal","rules_version":"v3"}`.

## Deploy

1. Build the image: `docker build -t fraud-audit-assist:<version> .` (CI builds it on every push).
2. Provide settings as environment variables or secrets; never bake them into the image:
   `FAA_ENVIRONMENT=production`, `FAA_DATABASE_URL=postgresql+psycopg://...`, `FAA_INGEST_API_KEY`,
   `FAA_ANALYST_API_KEY`, `FAA_LLM__PROVIDER`, `FAA_LLM__MODEL`, `FAA_LLM__API_KEY` (plus `FAA_LLM__ENDPOINT` for Azure),
   `FAA_LLM__INPUT_PRICE_PER_1K`, `FAA_LLM__OUTPUT_PRICE_PER_1K`.
3. Run migrations before the new version takes traffic: `faa db upgrade` (same image, same settings).
4. Start **one** instance (`faa serve --host 0.0.0.0`). Probe `/health/live` for liveness and `/health/ready` for
   readiness. Scrape `/metrics`.

Production startup fails fast, naming the problem, if API keys are missing, the LLM is the fake, SQLite is
configured, a provider setting is missing, or the pack and rules do not load.

## Day-to-day

| Task | How |
|---|---|
| See what rules are live | `GET /v1/rules` (analyst key) |
| Work the queue | `GET /v1/cases?status=AwaitingApproval` (worst first), `GET /v1/cases/{id}` |
| Decide a Case | `POST /v1/cases/{id}/decision` with `{"outcome","action","analyst_id","notes"}` |
| Read the audit trail | `GET /v1/cases/{id}/audit` |
| Seed a known fraud | `POST /v1/accounts/{id}/confirmed-fraud` with `{"decided_at"}` |
| Inspect links | `GET /v1/accounts/{id}/neighbourhood` |
| Try a rule change offline | Copy the rules file to the next version, then `faa scenario dry-run packs/<pack>/scenarios --rules vN` |
| Deploy a rule change | Add `vN.yaml`, review it, set `FAA_RULES_VERSION=vN` (or the pack's `default_rules`), restart |

## Spend control

- `FAA_INVESTIGATION__ENABLED=false` stops new Investigations; Cases still open and wait, marked `not_investigated`.
- `FAA_INVESTIGATION__DAILY_LIMIT` caps Investigations started per UTC day (default 20).
- Every model call logs `llm call` with tokens and estimated cost; `faa_llm_cost_total` sums it by agent.
- **Before replaying a Dataset into a real-LLM deployment, switch Investigations off.**

## Synthetic data

```bash
uv run faa scenario dry-run packs/investor_portal/scenarios              # score in memory, graph rules included
uv run faa scenario post packs/investor_portal/scenarios --api-url http://localhost:8000
uv run faa dataset generate --pack packs/digital_banking --seed 42 --accounts 1000
uv run faa dataset post datasets/digital_banking-seed-42-1000.jsonl --api-url http://localhost:8000
```

When the API has keys, pass `--api-key` (or `FAA_CLIENT_API_KEY`) with the ingest key for events, and
`--analyst-key` (or `FAA_CLIENT_ANALYST_KEY`) with the analyst key for seeding Confirmed Frauds. The server must run the same pack as the files you post.

## Reset data (development)

- SQLite: stop the service, delete `fraud_audit_assist.db*` (the `.checkpoints` file holds workflow state).
- Docker: `docker compose down -v`.
- Link Graph only: `faa db rebuild-links` with ingestion stopped. Alerts are append-only and are not re-scored.

## Common failures

| Symptom | Cause and fix |
|---|---|
| Startup: `Configuration error:` then `llm: LLM provider 'openai' needs an API key` | Set `FAA_LLM__API_KEY` (and `FAA_LLM__ENDPOINT` for Azure). `faa` exits with code 2 and lists every problem. |
| Startup: `Pack error: ... has no rules v9` | `FAA_RULES_VERSION` names a version the pack does not have; the message lists those it has. |
| Startup: `Unsafe production settings` | The message lists each problem: keys, fake LLM or SQLite. |
| Startup: `Rule R9: unknown event types` | A rules file names an event type the pack's `pack.yaml` does not define. |
| `422 unknown event types for pack` | The source sent an event type the running pack does not know; check `FAA_PACK_DIR`. |
| `401 missing or invalid API key` | Send `X-API-Key`; ingest and analyst keys are different on purpose. |
| `409 ... is Closed, not AwaitingApproval` | The Case was already decided, or is not yet investigated. |
| Cases stay `Open` with `not_investigated` | The budget guard: Investigations disabled or daily limit reached. Not re-queued automatically. |
| Cases stay `Investigating` | The run failed on infrastructure (see `investigation failed to run` in logs); it is re-queued at the next start, within budget. |
| Cases with `investigation_failed` | The LLM failed or replied badly twice; `failure` on the Case says why. Rules and Alerts are unaffected. |
| `database is locked` (SQLite) | SQLite is single-writer and for development; use PostgreSQL for anything concurrent. |
| Spans missing | Set `FAA_OTEL_EXPORTER_OTLP_ENDPOINT` to an OTLP/HTTP collector (for example `http://otel-collector:4318`). |

## Logs to look for

Every line is JSON with `request_id`. Useful events: `batch ingested`, `case opened`, `investigation awaiting
approval`, `investigation skipped by budget guard`, `llm call`, `llm call failed` (with `fallback_next`),
`investigation failed to run`, `confirmed fraud recorded`.
