# fraud-audit-assist

Rules-first fraud detection with AI investigation and a human in the loop, for any business that can describe its
events. Cheap, deterministic rules score every event; a link graph connects Accounts that share devices, phones or
payout accounts; Accounts that reach High get a Case, which two LangGraph agents investigate on masked data; a named
analyst decides, and every step is audited.

It is the Python counterpart of [vigil](https://github.com/NaveenManjappa/vigil) (C#, Microsoft Agent Framework),
rebuilt on FastAPI, SQLAlchemy and LangGraph, and made generic: everything business-specific lives in a
[Domain Pack](docs/domain-packs.md). Two packs ship: `investor_portal` (vigil's account-takeover and registration
fraud) and `digital_banking` (SIM-swap takeover, mule payees, application fraud).

## What happens to an event

```
POST /v1/events ─► store (idempotent) ─► update the Link Graph ─► score the Account and its Neighbours
                                                                        │
                                       Alert reaches High ─► Case ─► queue (after commit)
                                                                        │
     Investigator agent (tools: timeline, graph neighbourhood, baseline) ─► code validation ─► Reviewer agent
                                                                        │
                                  interrupt(): Case AwaitingApproval ─► analyst decides ─► Act + audit trail
```

Margaret's takeover from the investor portal pack, as the engine scores it (`faa scenario dry-run`):

| Step | Event | New Rule Hits | Score | Band |
|---|---|---|---|---|
| 1 | PasswordReset from D-77, RO | R0 first sighting +10; G2 shares D-77 with Confirmed Fraud ACC-PRIYA +25; G3 D-77 on 4 Accounts +20 | 55 | Medium |
| 2 | MfaEnrolled, 4 min later | R1 reset → MFA within 1h +30 | 85 | **High: Case opened** |
| 3 | EmailChanged | R2 reset → contact change within 24h +20 | 105 | Critical |
| 4 | BankDetailsChanged, next day | R3 +30; R7 same device did reset, contact and bank change within 14d +50 | 185 | Critical |

Flagged before the bank change, which is the goal.

## What is new compared with vigil

| Area | vigil | fraud-audit-assist |
|---|---|---|
| Scope | One business, hard-wired | Domain Packs: event catalogue, identifiers, typologies, actions and terminology are config (ADR-0017) |
| Rules | Two-step sequences, first sighting, three fixed graph queries | Multi-step sequences joined on a field, value conditions, velocity, configurable first sighting, graph rules by kind (ADR-0018) |
| Slow-burn fraud | Known miss | Caught by a joined 14-day sequence in investor portal v3 |
| Rings | One hop only | Optional multi-hop rings that follow chains of shared identifiers (ADR-0014) |
| Graph store | Neo4j, written beside Postgres (dual write) | A link table in the same transaction as the events: no dual write, no second database (ADR-0015) |
| Dry runs | Skip graph rules | Include graph rules, via an in-memory replay identical to live ingestion |
| Checkpointing | Resume only at Await Approval | Every step checkpointed; a crash mid-Investigation resumes where it stopped (ADR-0005) |
| Guardrails | Masking, ref validation, review | Plus masked-by-default attributes against prompt injection and a restrictive-action guardrail (ADR-0006, ADR-0008) |
| LLM calls | Provider switch, token logging | Gateway with timeouts, transient-only retries, fallback model, rate limit, cost per call (ADR-0009) |
| Concurrent decisions | Primary-key race | Claim-then-resume, with crash recovery (ADR-0019) |
| Evaluation | Full harness | A curated golden dataset first; the harness comes next (ADR-0016) |

## Quick start

Prerequisites: Python 3.12+ and [uv](https://docs.astral.sh/uv/). Docker is optional.

```bash
uv sync
uv run faa serve                     # http://127.0.0.1:8000/docs; SQLite and the free fake LLM by default
```

In a second terminal, post the investor portal Scenarios and watch Margaret's Case appear:

```bash
uv run faa scenario post packs/investor_portal/scenarios
curl -s "localhost:8000/v1/cases?status=AwaitingApproval" | python -m json.tool
curl -s localhost:8000/v1/cases/<case_id> | python -m json.tool      # unmasked Finding, refs resolved to events
curl -s -X POST localhost:8000/v1/cases/<case_id>/decision -H 'Content-Type: application/json' \
  -d '{"outcome":"ConfirmedFraud","action":"BlockBankChanges","analyst_id":"analyst-7","notes":"Confirmed by phone."}'
curl -s localhost:8000/v1/cases/<case_id>/audit | python -m json.tool
```

With PostgreSQL: `docker compose up --build` (migrates, then serves on port 8000).

Without API keys every endpoint is open, which the service allows only outside production and logs as a warning.
Set `FAA_INGEST_API_KEY` and `FAA_ANALYST_API_KEY`, then send `X-API-Key`.

### Use a real model

```bash
export FAA_LLM__PROVIDER=openai          # or azure_openai, anthropic
export FAA_LLM__MODEL=gpt-4.1-mini       # the deployment name for Azure
export FAA_LLM__API_KEY=...              # plus FAA_LLM__ENDPOINT for Azure
export FAA_LLM__INPUT_PRICE_PER_1K=0.0004 FAA_LLM__OUTPUT_PRICE_PER_1K=0.0016
```

Every new Case now costs tokens. `FAA_INVESTIGATION__DAILY_LIMIT` (default 20) caps Investigations per UTC day, and
every call logs its tokens and estimated cost. See [.env.example](.env.example) for every setting.

### Switch business

```bash
FAA_PACK_DIR=packs/digital_banking uv run faa serve
uv run faa scenario post packs/digital_banking/scenarios --pack packs/digital_banking
```

## Commands

| Command | Does |
|---|---|
| `faa serve` | Runs the API |
| `faa db upgrade` | Applies Alembic migrations to `FAA_DATABASE_URL` |
| `faa db rebuild-links` | Rebuilds the Link Graph from the events |
| `faa scenario dry-run <files or dirs> [--rules vN]` | Scores Scenarios in memory, graph rules included, step by step |
| `faa scenario post <files or dirs>` | Posts Scenarios to a running API and seeds their Confirmed Frauds |
| `faa dataset generate --pack <dir> --seed 42 --accounts 1000` | Writes a seeded, labelled synthetic Dataset |
| `faa dataset post <file>` | Posts a Dataset in event-time order, without its Labels |

## API

| Method | Route | Role | Purpose |
|---|---|---|---|
| `POST` | `/v1/events` | ingest | Ingest a batch (up to 1,000), idempotent on `event_id` |
| `GET` | `/v1/alerts?band=&account_id=` | analyst | Alerts, worst first |
| `GET` | `/v1/alerts/{id}` | analyst | An Alert with every Rule Hit and its evidence |
| `GET` | `/v1/cases?status=` | analyst | The Case queue: band, then Score, then time opened |
| `GET` | `/v1/cases/{id}` | analyst | Unmasked Finding, flags, decision, Interventions |
| `POST` | `/v1/cases/{id}/decision` | analyst | Decide a Case awaiting approval |
| `GET` | `/v1/cases/{id}/audit` | analyst | The audit trail |
| `GET` | `/v1/accounts/{id}/timeline` | analyst | An Account's events |
| `GET` | `/v1/accounts/{id}/neighbourhood` | analyst | Linked identifiers and Accounts |
| `POST` | `/v1/accounts/{id}/confirmed-fraud` | analyst | Seed a known fraud |
| `GET` | `/v1/rules` | analyst | The live Rule Set |
| `GET` | `/health/live`, `/health/ready`, `/metrics` | none | Operations |

OpenAPI docs: `/docs`.

## Tests and checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run mypy
uv run pytest                                            # SQLite; no network, no LLM
FAA_TEST_POSTGRES_URL=postgresql+psycopg://user@host/db uv run pytest   # also runs every DB test on PostgreSQL
```

No test calls a real LLM: the scripted fake model drives every workflow path (approval, code rejection, Reviewer
rejection, Review Flag, unparseable replies, outages, retries, bounded tool loops). CI runs lint, types, tests on
PostgreSQL, a dependency audit, bandit and a Docker build on every push.

## Golden dataset

[`evals/golden/`](evals/golden/README.md) holds 26 hand-curated cases across both packs with expected detection
(exact) and expected investigation outcomes (ranges), including known misses and a prompt-injection case. The
harness that scores against it comes next.

## Layout

| Path | What |
|---|---|
| `src/fraud_audit_assist/` | The service; see [docs/architecture.md](docs/architecture.md) for the modules |
| `packs/` | Domain Packs: `pack.yaml`, `rules/vN.yaml`, `personas.yaml`, `scenarios/` |
| `migrations/` | Alembic migrations |
| `evals/golden/` | Golden dataset |
| `tests/unit`, `tests/integration` | Pure logic; database, API and migrations (SQLite, and PostgreSQL when configured) |
| `docs/` | [Architecture](docs/architecture.md), [runbook](docs/runbook.md), [Domain Packs](docs/domain-packs.md), [ADRs](docs/adr/) |
| `GLOSSARY.md` | The domain language |

## Deploy

See the [runbook](docs/runbook.md#deploy): build the image, provide settings as secrets, run `faa db upgrade`, start
one instance, probe `/health/ready`, scrape `/metrics`.
