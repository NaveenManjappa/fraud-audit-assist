# fraud-audit-assist user guide

This guide tells you how to set up, configure and operate fraud-audit-assist from the start to the end.
It is for engineers and operators who are new to fraud-audit-assist.

Part A tells you how to prepare the system.
Part B takes one fraud case through the full system.
Each step gives the command, the result that you will see, and the configuration or code that controls the step.

The guide uses the terms in [GLOSSARY.md](../GLOSSARY.md).
A term with a capital letter, for example Alert, Case or Finding, has the meaning that the glossary gives.
For the design of each part, read [architecture.md](architecture.md).
If a step fails, read [runbook.md](runbook.md).

The example outputs in this guide come from a real run on 25 October 2026, with the free fake LLM provider.
Identifiers such as Case IDs, Alert IDs and times are different on your computer.

## Contents

**Part A: Set up and configure**

1. [What fraud-audit-assist does](#1-what-fraud-audit-assist-does)
2. [Before you start](#2-before-you-start)
3. [Set up an LLM provider (optional)](#3-set-up-an-llm-provider-optional)
4. [Configure fraud-audit-assist](#4-configure-fraud-audit-assist)

**Part B: Operate fraud-audit-assist from the start to the end**

5. [Start the service](#5-start-the-service)
6. [Send events](#6-send-events)
7. [Read the Alert](#7-read-the-alert)
8. [Examine the Link Graph](#8-examine-the-link-graph)
9. [Follow the Case and the Investigation](#9-follow-the-case-and-the-investigation)
10. [Record the Analyst Decision](#10-record-the-analyst-decision)
11. [Monitor the service](#11-monitor-the-service)
12. [Operate with a real LLM](#12-operate-with-a-real-llm)
13. [Use a different Domain Pack](#13-use-a-different-domain-pack)
14. [Make synthetic data](#14-make-synthetic-data)
15. [Change a Rule safely](#15-change-a-rule-safely)
16. [Use the golden dataset](#16-use-the-golden-dataset)
17. [Deploy to production](#17-deploy-to-production)
18. [Reset and recover](#18-reset-and-recover)

---

# Part A: Set up and configure

## 1. What fraud-audit-assist does

fraud-audit-assist finds fraud in the sequence of actions on an online account.
It finds the fraud before the fraudster moves the money.

One Domain Pack tells the service about one business.
Two Domain Packs come with the service:

| Domain Pack | Business | Fraud that it finds |
|---|---|---|
| `investor_portal` | Shareholders view holdings, change payment details, and sell shares | Account takeover and registration fraud |
| `digital_banking` | Customers bank online and send payments | SIM-swap takeover, mule payees and application fraud |

The service does these tasks in sequence:

1. It receives each event, for example a password reset or an email change.
2. It writes the event to the database. It also writes the identifiers of the event to the Link Graph.
3. It scores the Account with Rules. The result is an Alert with a Score and a Risk Band.
4. It opens a Case when an Alert reaches the High band.
5. Two AI agents investigate the Case. The Investigator writes a Finding. The Reviewer checks the Finding.
6. The Case stops and waits for an analyst.
7. The analyst records a decision. The service records the Intervention and the audit trail.

The Rules are cheap, and they examine every event.
The AI agents examine only the risky Accounts.
A person makes each important decision.

The system has these parts:

| Part | What it does |
|---|---|
| The API (`faa serve`) | Receives events, scores them, runs Investigations, and gives the HTTP endpoints |
| The database | Keeps all events, Links, Alerts, Cases, decisions and Audit Entries. Use SQLite on your computer and PostgreSQL in production. |
| The checkpoint store | Keeps the state of each Investigation, so that a Case survives a restart |
| The LLM | Writes and reviews Findings. The default is a free fake provider. |
| The `faa` command | Starts the API, migrates the database, sends test events and makes test data |

## 2. Before you start

### 2.1 Install the tools

Install these tools on your computer:

| Tool | Version | Use |
|---|---|---|
| Python | 3.12 or later | Runs the service |
| uv | 0.11 or later | Installs the exact package versions from `uv.lock` |
| Git | Current | Gets the code |
| curl | Any | Sends requests to the API |
| Docker | Current | Optional. Runs PostgreSQL and the container image |

Install uv with the procedure on the [uv website](https://docs.astral.sh/uv/getting-started/installation/).

### 2.2 Get the code

```bash
git clone https://github.com/NaveenManjappa/fraud-audit-assist.git
cd fraud-audit-assist
uv sync
```

`uv sync` makes a virtual environment in `.venv/` and installs the packages.
It installs the versions that `uv.lock` gives, so each computer gets the same versions.

Do all the commands in this guide from the repository root.
The commands read the `packs/`, `migrations/` and `alembic.ini` files from the current folder.

Note: The commands in this guide start with `uv run`. This runs the command in the virtual environment.

### 2.3 Know the folders

| Folder | Contents |
|---|---|
| `src/fraud_audit_assist/` | The service. [architecture.md](architecture.md) describes each module. |
| `packs/` | The Domain Packs. Each pack has `pack.yaml`, `rules/vN.yaml`, `personas.yaml` and `scenarios/`. |
| `migrations/` | The database migrations |
| `evals/golden/` | The golden dataset for evaluation |
| `tests/` | The unit tests and the integration tests |
| `docs/` | This guide, the architecture, the runbook, the Domain Pack guide and the ADRs |

## 3. Set up an LLM provider (optional)

**PAID STEPS.** The steps in this section use a service that costs money.

The service has four LLM providers:

| Provider | Setting value | Cost | Use |
|---|---|---|---|
| Fake | `fake` | Free | The default. It gives scripted answers. It needs no key. Use it for development, demos and tests. |
| OpenAI | `openai` | Paid | A model from OpenAI |
| Azure OpenAI | `azure_openai` | Paid | A model deployment in your Azure subscription |
| Anthropic | `anthropic` | Paid | A Claude model from Anthropic |

Do this section only if you want real AI results.
If you do not, go to [section 4](#4-configure-fraud-audit-assist) and keep the fake provider.

The provider websites change often.
If a screen is different, use the documentation page that each step gives.

### 3.1 Set a spending limit before you spend money

Do this step first.

1. Sign in to the console of your provider.
2. Find the billing or budget page.
3. Set a monthly limit or a budget alert. A limit of £10 or $10 is enough to try the service.

| Provider | Where to set the limit |
|---|---|
| OpenAI | The **Limits** page of your organization in the [OpenAI platform](https://platform.openai.com) |
| Azure OpenAI | **Cost Management + Billing**, then **Budgets**, in the [Azure portal](https://portal.azure.com) |
| Anthropic | The **Limits** page of the [Anthropic Console](https://console.anthropic.com) |

The service also has its own limit: `FAA_INVESTIGATION__DAILY_LIMIT`.
It stops new Investigations when the number for one UTC day reaches the limit.
[Section 9.3](#93-know-the-budget-guard) tells you more.

Note: Provider cost data can be some hours late.
The service logs the estimated cost of each call, so that you can see the cost immediately.

### 3.2 Get a model and a key

Use one of these procedures.

**Procedure 1: OpenAI**

1. Sign in to the [OpenAI platform](https://platform.openai.com).
2. Open **API keys**.
3. Select **Create new secret key**.
4. Copy the key. The platform shows it only one time.
5. Choose a small, cheap chat model that supports tool calls, for example `gpt-4.1-mini`.

**Procedure 2: Azure OpenAI**

1. In the [Azure portal](https://portal.azure.com), create an **Azure OpenAI** resource.
2. In the [Foundry portal](https://ai.azure.com), deploy a chat model, for example `gpt-4.1-mini`.
3. Write down the **deployment name**, for example `faa-chat`.
4. In the Azure portal, open the resource. Select **Keys and Endpoint**.
5. Copy the **Endpoint**. It has the form `https://<name>.openai.azure.com/`.
6. Copy **KEY 1**.

Reference: [Create and deploy an Azure OpenAI resource](https://learn.microsoft.com/azure/ai-foundry/openai/how-to/create-resource).

CAUTION: For Azure OpenAI, the service uses the **deployment name**, not the model name.
If you use the model name, Azure returns the error 404.

**Procedure 3: Anthropic**

1. Sign in to the [Anthropic Console](https://console.anthropic.com).
2. Open **API Keys**.
3. Select **Create Key**.
4. Copy the key.
5. Choose a small, cheap model, for example `claude-haiku-5-5`.

WARNING: Keep the key secret.
Do not put it in a file that Git tracks.
Do not paste it into a chat or a ticket.

### 3.3 Find the token prices

The service calculates the estimated cost of each call from two prices.
Each price is for 1,000 tokens, in the currency of your bill.

1. Open the pricing page of your provider.
2. Find your model.
3. Read the input price and the output price for 1 million tokens.
4. Divide each price by 1,000. This gives the price for 1,000 tokens.

Example: an input price of $0.40 for 1 million tokens is $0.0004 for 1,000 tokens.

[Section 4](#4-configure-fraud-audit-assist) tells you where to put the prices.

### 3.4 Know how the service calls the model

The service builds the model client in `src/fraud_audit_assist/agents/llm/factory.py`.
This is the part for Azure OpenAI:

```python
case "azure_openai":
    from langchain_openai import AzureChatOpenAI

    return AzureChatOpenAI(
        azure_endpoint=settings.endpoint,
        azure_deployment=model,
        api_version=settings.api_version,
        api_key=api_key,
        timeout=settings.timeout_seconds,
        **common,
    )
```

`common` sets `max_retries=0` for the provider SDK.
The service does the retries itself, in one place for all providers (ADR-0009).
Thus the number of attempts never multiplies.

## 4. Configure fraud-audit-assist

### 4.1 Where the settings are

All settings are environment variables. Each name starts with `FAA_`.
A setting in a group uses two underscores. For example, the LLM provider is `FAA_LLM__PROVIDER`.

A setting can come from two places:

| Place | Used for | In Git |
|---|---|---|
| Environment variables | All settings, on any computer | No |
| A `.env` file in the repository root | Settings for your computer | No. `.gitignore` excludes it. |

`.env.example` lists every setting with its default value.
To make your `.env` file, copy it:

```bash
cp .env.example .env
```

An environment variable replaces the same setting in `.env`.

### 4.2 All the settings

| Setting | Default | Change it when |
|---|---|---|
| `FAA_ENVIRONMENT` | `development` | You deploy to production: set `production` ([section 17](#17-deploy-to-production)) |
| `FAA_DATABASE_URL` | `sqlite+aiosqlite:///./fraud_audit_assist.db` | You use PostgreSQL: `postgresql+psycopg://<user>:<password>@<host>:5432/<database>` |
| `FAA_PACK_DIR` | `packs/investor_portal` | You use a different Domain Pack ([section 13](#13-use-a-different-domain-pack)) |
| `FAA_RULES_VERSION` | The `default_rules` of the pack | You deploy a different Rules Version ([section 15](#15-change-a-rule-safely)) |
| `FAA_INGEST_API_KEY` | none | Always, except on your own computer. The key for systems that send events. |
| `FAA_ANALYST_API_KEY` | none | Always, except on your own computer. The key for analysts. |
| `FAA_LOG_LEVEL` | `INFO` | You need more detail: set `DEBUG` |
| `FAA_LOG_JSON` | `true` | You want easy-to-read logs on your computer: set `false` |
| `FAA_OTEL_EXPORTER_OTLP_ENDPOINT` | none | You want traces: the URL of an OpenTelemetry collector ([section 11.3](#113-see-the-traces)) |
| `FAA_LLM__PROVIDER` | `fake` | You want real AI results: `openai`, `azure_openai` or `anthropic` |
| `FAA_LLM__MODEL` | `gpt-4.1-mini` | Your model is different. For Azure OpenAI, use the deployment name. |
| `FAA_LLM__API_KEY` | none | You use a paid provider: the key from [3.2](#32-get-a-model-and-a-key) |
| `FAA_LLM__ENDPOINT` | none | You use Azure OpenAI: the endpoint from [3.2](#32-get-a-model-and-a-key) |
| `FAA_LLM__API_VERSION` | `2024-10-21` | Azure OpenAI only. Your resource needs a different API version. |
| `FAA_LLM__TIMEOUT_SECONDS` | `30` | Your model is slow |
| `FAA_LLM__MAX_RETRIES` | `2` | You want more or fewer retries after a temporary error |
| `FAA_LLM__REQUESTS_PER_SECOND` | none | Your provider limits the rate of requests |
| `FAA_LLM__INPUT_PRICE_PER_1K` | `0` | You use a paid provider: the price from [3.3](#33-find-the-token-prices) |
| `FAA_LLM__OUTPUT_PRICE_PER_1K` | `0` | You use a paid provider |
| `FAA_LLM__MAX_TOOL_ROUNDS` | `6` | You want to limit the tool calls of the Investigator more or less |
| `FAA_LLM__FALLBACK_PROVIDER`, `FAA_LLM__FALLBACK_MODEL`, `FAA_LLM__FALLBACK_API_KEY` | none | You want a second model when the first model fails |
| `FAA_INVESTIGATION__ENABLED` | `true` | You send a large Dataset and you do not want Investigations |
| `FAA_INVESTIGATION__DAILY_LIMIT` | `20` | You want more or fewer Investigations each UTC day |
| `FAA_INVESTIGATION__MAX_ATTEMPTS` | `2` | You want more or fewer attempts to write a Finding. Each attempt costs model calls. |

The class `Settings` in `src/fraud_audit_assist/config.py` defines all these settings.

### 4.3 Know how the service examines the settings

The service examines all settings when it starts.
If a setting is wrong, the service does not start. It shows each problem and stops with exit code 2.

For example, the provider `openai` without a key gives this result:

```text
$ FAA_LLM__PROVIDER=openai uv run faa serve
Configuration error:
  llm: LLM provider 'openai' needs an API key (FAA_LLM__API_KEY)
```

A Rules Version that does not exist gives this result:

```text
$ FAA_RULES_VERSION=v9 uv run faa serve
Pack error: Pack investor_portal has no rules v9; available: ['v1', 'v2', 'v3']
```

In production, the service refuses settings that are not safe:

```text
$ FAA_ENVIRONMENT=production uv run faa serve
Configuration error:
  settings: Unsafe production settings: FAA_INGEST_API_KEY and FAA_ANALYST_API_KEY must be set; the fake LLM provider is for development and tests only; SQLite is single-writer and for development only; use PostgreSQL
```

### 4.4 Know the API keys

The API has two roles. Each role has its own key:

| Role | Key setting | Can do |
|---|---|---|
| ingest | `FAA_INGEST_API_KEY` | Send events (`POST /v1/events`). Nothing else. |
| analyst | `FAA_ANALYST_API_KEY` | Read Alerts and Cases, record decisions, read the audit trail, seed a Confirmed Fraud |

A system that sends events gets the ingest key only.
Thus a lost ingest key cannot read or decide Cases.

The client sends the key in the `X-API-Key` header.

If you set no key, every endpoint is open.
The service permits this only outside production, and it logs this warning:

```json
{"event": "API keys are not set: every endpoint is open (development only)", "level": "warning", "logger": "fraud_audit_assist.api.app", "timestamp": "2026-10-25T10:43:12.604488Z"}
```

### 4.5 Configure the service for a paid provider

Do these steps only after [section 3](#3-set-up-an-llm-provider-optional).

1. Open `.env`.
2. Change the LLM settings. Use one of these examples:

   ```bash
   # OpenAI
   FAA_LLM__PROVIDER=openai
   FAA_LLM__MODEL=gpt-4.1-mini
   FAA_LLM__API_KEY=<your key>
   FAA_LLM__INPUT_PRICE_PER_1K=0.0004
   FAA_LLM__OUTPUT_PRICE_PER_1K=0.0016
   ```

   ```bash
   # Azure OpenAI
   FAA_LLM__PROVIDER=azure_openai
   FAA_LLM__MODEL=faa-chat                         # the deployment name
   FAA_LLM__ENDPOINT=https://<name>.openai.azure.com/
   FAA_LLM__API_KEY=<KEY 1>
   ```

   ```bash
   # Anthropic
   FAA_LLM__PROVIDER=anthropic
   FAA_LLM__MODEL=claude-haiku-5-5
   FAA_LLM__API_KEY=<your key>
   ```

3. Set the prices from [3.3](#33-find-the-token-prices).
4. Make sure that `git status` does not show `.env`.

---

# Part B: Operate fraud-audit-assist from the start to the end

Part B follows one fraud case from the first event to the decision.
The case is Margaret, from the `investor_portal` Domain Pack.

In summary, Margaret logs in from her laptop in the UK for many months.
A fraudster gets her email password.
The fraudster uses device `D-77` in Romania. In 10 minutes, the fraudster resets her password, enrols a new MFA phone and changes her email.
The next day, the fraudster changes her bank details.
Three other Accounts used device `D-77` before. One of them, Priya, is a Confirmed Fraud.

Use the fake provider for Part B, except in [section 12](#12-operate-with-a-real-llm).

## 5. Start the service

### 5.1 Start the API

You can start the API in two ways. Use one of the two procedures.

**Procedure 1: on your computer, with SQLite**

1. Set the two API keys, so that you can see how the roles work:

   ```bash
   export FAA_INGEST_API_KEY=ingest-key-local
   export FAA_ANALYST_API_KEY=analyst-key-local
   ```

2. Start the API:

   ```bash
   uv run faa serve
   ```

3. Wait for these log lines:

   ```json
   {"pack": "investor_portal", "rules_version": "v3", "llm_provider": "fake", "model": "scripted-fake", "auth_enabled": true, "event": "service started", "level": "info", "logger": "fraud_audit_assist.api.app", "timestamp": "2026-10-25T10:42:28.862931Z"}
   {"event": "Uvicorn running on http://127.0.0.1:8000 (Press CTRL+C to quit)", "level": "info", "logger": "uvicorn.error", "timestamp": "2026-10-25T10:42:28.864593Z"}
   ```

In development with SQLite, the API makes the database tables when it starts.
The database is the file `fraud_audit_assist.db`.
The checkpoint store is the file `fraud_audit_assist.db.checkpoints`.

**Procedure 2: in Docker, with PostgreSQL**

```bash
docker compose up --build
```

This command starts PostgreSQL, migrates the database, and starts the API on port 8000.
`docker-compose.yml` defines the two containers.
The data stays in the Docker volume `faa-db` when you stop the containers.

Note: The Docker procedure sets no API keys. Every endpoint is open. Use it only on your computer.

### 5.2 Make sure that the service is healthy

Do this command:

```bash
curl http://localhost:8000/health/ready
```

```json
{"status":"ok","pack":"investor_portal","rules_version":"v3"}
```

The result tells you the Domain Pack and the Rules Version that are in use.
If the database is not available, the endpoint returns HTTP 503.

These addresses are on the API:

| Address | What |
|---|---|
| http://localhost:8000/docs | The API reference, where you can try each endpoint |
| http://localhost:8000/health/live | `ok` if the process runs |
| http://localhost:8000/health/ready | `ok` if the process runs and the database answers |
| http://localhost:8000/metrics | The metrics, in Prometheus format |

### 5.3 Make sure that the keys work

Ask for the Cases without a key:

```bash
curl -i http://localhost:8000/v1/cases
```

```text
HTTP/1.1 401 Unauthorized
{"detail":"missing or invalid API key"}
```

The API refuses the request. This is correct.
In the rest of Part B, the analyst commands use this header:

```bash
A="X-API-Key: analyst-key-local"
```

## 6. Send events

### 6.1 Know the Scenario file

A Scenario is a YAML file that describes one Account journey.
Margaret's Scenario is `packs/investor_portal/scenarios/margaret-ato.yaml`:

```yaml
name: margaret-ato
description: >-
  After months of logins from her laptop in Reading, a fraudster with Margaret's email password takes over her
  account from a new device in Romania, then redirects payouts the next day. The device, D-77, was already used on
  three other Accounts (derek-reset, priya-ato, lena-reset), one of them Confirmed Fraud.
account_id: ACC-MARGARET
customer_id: HOLDER-MARGARET
baseline: { device: D-MARGARET-LAPTOP, ip: 203.0.113.51, country: GB }
actors:
  fraudster: { device: D-77, ip: 198.51.100.77, country: RO }
steps:
  - { after: 0m, type: PasswordReset, as: fraudster }
  - { after: 4m, type: MfaEnrolled, as: fraudster }
  - { after: 10m, type: EmailChanged, as: fraudster, attributes: { new_email: m.attacker@example.net } }
  - { after: 1d5h, type: BankDetailsChanged, as: fraudster, attributes: { new_bank_account_hash: BA-9f31 } }
label: { kind: AccountTakeover, intent: "Classic recovery takeover from a device already linked to Confirmed Fraud." }
```

| Field | Required | Meaning |
|---|---|---|
| `name` | Yes | A short name in lower case with hyphens |
| `description` | Yes | A sentence that tells the story of the journey |
| `account_id` | Yes | The Account that the events belong to |
| `customer_id` | No | The owner of the Account. The default is `CUST-` and the `account_id`. |
| `start` | No | The time of the first step. The default is `2026-09-21T09:00:00Z`. |
| `baseline` | No | The usual `device`, `ip` and `country` of the customer. The service makes `weeks` (default 13) of weekly logins from them. Leave it out for a new Account. |
| `actors` | No | Other people who act on the Account. Each has a `device`, an `ip`, a `country`, and a `channel` (default `web`). |
| `steps` | Yes | The events. `after` is the time from the start, for example `10m` or `1d5h`. `type` is an event type of the pack. `as` names the actor. Without `as`, the customer acts from the baseline. |
| `attributes` | No | Data for one step, for example `new_email` or `new_bank_account_hash` |
| `label` | No | The ground truth: `kind` (a typology of the pack, or `Genuine`) and a one-line `intent` |
| `confirmed_fraud` | No | A date. After the events, the service records the Account as a Confirmed Fraud with source Seed. |

The parser is strict.
It rejects a file with an unknown field, an unknown event type or an unknown actor.
The error gives the file name and the step.

The other Scenarios in the same folder set up Margaret's story:

- `priya-ato.yaml` uses `D-77`. It has `confirmed_fraud`, so Priya becomes a Confirmed Fraud.
- `derek-reset.yaml` and `lena-reset.yaml` also use `D-77`.
- `patel-house-move.yaml` is a genuine Holder who moves house.

### 6.2 Send the Scenarios

Do this command:

```bash
uv run faa scenario post packs/investor_portal/scenarios \
  --api-key ingest-key-local --analyst-key analyst-key-local
```

```text
derek-reset: 15 ingested, 0 already ingested
priya-ato: 17 ingested, 0 already ingested
priya-ato: Confirmed Fraud seeded
lena-reset: 15 ingested, 0 already ingested
margaret-ato: 17 ingested, 0 already ingested
patel-house-move: 15 ingested, 0 already ingested
```

The command sends the Scenarios in the sequence of their first event.
Thus D-77's earlier Accounts are in the Link Graph before Margaret's events arrive.
The command uses the ingest key for the events and the analyst key for the Confirmed Fraud.

| Option | Environment variable | Default | Use |
|---|---|---|---|
| `--api-url` | `FAA_API_URL` | `http://127.0.0.1:8000` | The address of the API |
| `--api-key` | `FAA_CLIENT_API_KEY` | none | The ingest key |
| `--analyst-key` | `FAA_CLIENT_ANALYST_KEY` | none | The analyst key, for `confirmed_fraud` |
| `--pack` | `FAA_PACK_DIR` | `packs/investor_portal` | The pack that the command uses to read the files |

You can send the same Scenario again.
The service identifies each event by its `event_id`, and it does not store an event two times:

```text
$ uv run faa scenario post packs/investor_portal/scenarios/margaret-ato.yaml --api-key ingest-key-local --analyst-key analyst-key-local
margaret-ato: 0 ingested, 17 already ingested
```

### 6.3 Send events from your own system

A source system sends events to `POST /v1/events`, with the ingest key:

```bash
curl -X POST http://localhost:8000/v1/events \
  -H "X-API-Key: ingest-key-local" -H "Content-Type: application/json" \
  -d '{"events": [{
        "event_id": "6c1f0a3e-5d1b-4a1e-9a77-0f1b2c3d4e5f",
        "account_id": "ACC-1001",
        "customer_id": "CUST-1001",
        "type": "PasswordReset",
        "occurred_at": "2026-10-25T09:00:00Z",
        "device_id": "D-ABC123",
        "ip_address": "198.51.100.10",
        "country": "GB",
        "channel": "web",
        "attributes": {}
      }]}'
```

| Field | Required | Rule |
|---|---|---|
| `event_id` | Yes | A UUID. Use the same value when you send the event again. |
| `account_id`, `customer_id` | Yes | 1 to 128 characters |
| `type` | Yes | An event type of the pack. The service refuses all other types. |
| `occurred_at` | Yes | The time at the source, with a time zone. A time without a time zone is refused. |
| `device_id`, `ip_address`, `country`, `channel` | No | `country` has two letters. `channel` defaults to `web`. |
| `attributes` | No | Data for the event type, for example `new_email` or `amount` |

One request can have 1 to 1,000 events.

### 6.4 Know what happens to each batch

`POST /v1/events` does all these tasks in one database transaction.
This is the main method, in `src/fraud_audit_assist/services/ingestion.py`:

```python
batch_accounts = sorted({e.account_id for e in events})
await self._db.lock_accounts(session, batch_accounts)

inserted = await EventRepository(session).append(events, self._clock())
...
graph = LinkRepository(session)
links = links_from(events, self._pack.pack)
await graph.upsert(links)

touched = list(batch_accounts)
if self._pack.rules.uses_graph:
    kinds = [i.kind for i in self._pack.pack.linking_identifiers]
    extra = await neighbours(links, batch_accounts, graph, kinds, ring_hops(self._pack.rules))
    # Neighbours are only known after the links exist, so their locks come second, again in sorted order.
    await self._db.lock_accounts(session, extra)
    touched += extra

for account_id in touched:
    result.opened_cases.extend(await self._rescore(session, account_id))
```

1. The service locks each Account in the batch, so that two batches cannot change one Account at the same time.
2. The service adds the new events. It ignores an event that it has already.
3. The service adds the Links to the Link Graph. A Link is the first use of a device, an email, a phone or a bank account by an Account.
4. The service finds the Neighbours. A Neighbour is another Account that shares one of these identifiers.
5. The service scores each Account in the batch and each Neighbour.
6. The service commits the transaction.
7. After the commit, the service puts each new Case in the Investigation queue.

If one step fails, the service writes nothing. The client can send the batch again.

The service writes one log line for each batch:

```json
{"ingested": 17, "duplicates": 0, "rescored": 2, "cases_opened": 1, "event": "batch ingested", "path": "/v1/events", "method": "POST", "request_id": "27b8649a47de469b8061d4f40900bf04", "level": "info", "logger": "fraud_audit_assist.services.ingestion", "timestamp": "2026-10-25T10:42:29.554885Z"}
```

## 7. Read the Alert

### 7.1 Get the Alerts

```bash
curl -H "$A" http://localhost:8000/v1/alerts
```

After you send the Scenarios, the result has four Alerts:

| Account | Score | Band |
|---|---|---|
| ACC-MARGARET | 185 | Critical |
| ACC-PRIYA | 160 | Critical |
| ACC-DEREK | 85 | High |
| ACC-LENA | 75 | High |

Mr Patel has no Alert. His events agree with no Rule.

The list shows the worst Alerts first.
To get only one band or one Account, add a filter:

```bash
curl -H "$A" "http://localhost:8000/v1/alerts?band=High"
curl -H "$A" "http://localhost:8000/v1/alerts?account_id=ACC-MARGARET"
```

The filters `limit` (1 to 500, default 50) and `offset` give one page of results.

### 7.2 Read Margaret's Alert

```bash
curl -H "$A" http://localhost:8000/v1/alerts/<alert id>
```

This is the result, with four Rule Hits removed so that it is shorter:

```json
{
    "alert_id": "242327cc-2d46-4969-9a9d-024a9ccfa591",
    "account_id": "ACC-MARGARET",
    "score": 185,
    "band": "Critical",
    "rules_version": "v3",
    "opened_at": "2026-10-25T10:42:29.754919Z",
    "updated_at": "2026-10-25T10:42:29.754919Z",
    "hits": [
        {
            "rule_id": "R0",
            "points": 10,
            "evidence_event_ids": ["fab06833-3d08-5566-a52f-e1a2890f5e5a"],
            "triggered_at": "2026-09-21T09:00:00Z",
            "explanation": "First sighting on this Account: new device D-77, new country RO"
        },
        {
            "rule_id": "G2",
            "points": 25,
            "evidence_event_ids": ["b56f13c0-2bad-531a-8a4e-83558ed188cd", "fab06833-3d08-5566-a52f-e1a2890f5e5a"],
            "triggered_at": "2026-09-21T09:00:00Z",
            "explanation": "Shares device D-77 with Confirmed Fraud Account ACC-PRIYA"
        },
        {
            "rule_id": "R1",
            "points": 30,
            "evidence_event_ids": ["fab06833-3d08-5566-a52f-e1a2890f5e5a", "cc625467-11ae-548f-9748-ae1bbcb28dff"],
            "triggered_at": "2026-09-21T09:04:00Z",
            "explanation": "Password reset then MFA enrolment, 4 min apart; window 1h"
        }
    ]
}
```

Each Rule Hit has these fields:

| Field | Meaning |
|---|---|
| `rule_id` and `points` | The Rule and the points that it adds to the Score |
| `evidence_event_ids` | The events that caused the hit. The last event completed the pattern. An analyst can find each event in the timeline. |
| `triggered_at` | The event time of the hit, not the time when the service calculated it |
| `explanation` | A sentence that tells why the Rule agrees with the events |

The `rules_version` tells you which Rules file opened the Alert.
The service never calculates an old Alert again with a newer Rules file.

To see all events of the Account, do this command:

```bash
curl -H "$A" http://localhost:8000/v1/accounts/ACC-MARGARET/timeline
```

### 7.3 Know how the Score increases

The Alert collects the Rule Hits in sequence of event time:

| Event | New Rule Hits | Score | Band |
|---|---|---|---|
| `PasswordReset` from D-77 in Romania | R0 +10, G2 +25, G3 +20 | 55 | Medium |
| `MfaEnrolled`, 4 minutes later | R1 +30 | 85 | **High. The service opens a Case.** |
| `EmailChanged`, 10 minutes after the reset | R2 +20 | 105 | Critical |
| `BankDetailsChanged`, the next day | R3 +30, R7 +50 | 185 | Critical |

The Alert reached High before the bank change. This is the purpose of the service.

These rules apply to the Score:

- Each Rule adds its points only one time in an Alert.
- The Score never decreases. A band can only go up.
- A Rule Hit stays in the same Alert if it occurs at most 7 days after the previous hit. A longer gap starts a new Alert.

### 7.4 Know the Rules file

The Rules are in `packs/investor_portal/rules/v3.yaml`. This is the start of the file, and three of its Rules:

```yaml
version: v3
risk_period: 7d
bands: { medium: 30, high: 60, critical: 90 }

rules:
  - id: R1
    name: Recovery takeover
    kind: sequence
    steps:
      - types: [PasswordReset]
      - types: [MfaEnrolled]
    window: 1h
    points: 30
    explanation: "Password reset then MFA enrolment, {elapsed} apart; window {window}"

  - id: R7
    name: Slow takeover on one device
    kind: sequence
    steps:
      - types: [PasswordReset]
      - types: [EmailChanged, PhoneChanged]
      - types: [BankDetailsChanged]
    window: 14d
    join_on: [device_id]
    points: 50
    explanation: "Password reset, contact change and bank-detail change from the same device, {elapsed} apart; window {window}"

  - id: G3
    name: Ring indicator
    kind: ring
    identifier_kinds: [device, email, phone, bank_account]
    account_threshold: 3
    max_hops: 1
    points: 20
    explanation: "{identifier} {value} is shared by {accounts} Accounts; Ring threshold {threshold}"
```

| Field | Meaning |
|---|---|
| `version` | The Rules Version. Every Alert and every Rule Hit records it. |
| `risk_period` | The longest gap between two hits in one Alert |
| `bands` | The lowest Score for each band. A Score below 30 is Low. |
| `kind` | One of six kinds. The next table describes them. |
| `steps` | For a sequence: the event types of each step, in sequence. A step can list alternatives and a `where` condition. |
| `window` | The longest time from the first step to the last step. The time is inclusive. |
| `join_on` | All steps must have the same value in this field, for example the same device |

The six kinds of Rule are these:

| Kind | Agrees when |
|---|---|
| `sequence` | Two to six steps occur in sequence inside the Window |
| `first_sighting` | An event has a value (for example a device or a country) that the Account has not used before |
| `velocity` | At least `count` matching events occur inside the Window |
| `shared_identifier` | The Account shares an identifier with an Account of a different customer |
| `fraud_proximity` | The Account shares an identifier with a Confirmed Fraud Account |
| `ring` | At least `account_threshold` Accounts share one identifier, or are within `max_hops` shared identifiers |

[domain-packs.md](domain-packs.md) gives every field of every kind.

To see the Rules that are in use, do this command:

```bash
curl -H "$A" http://localhost:8000/v1/rules
```

### 7.5 Try a Rule on a Scenario without the API

You can score Scenarios in memory. The API does not have to run.

```bash
uv run faa scenario dry-run packs/investor_portal/scenarios/margaret-ato.yaml --pack packs/investor_portal
```

```text
Rules investor_portal v3
## margaret-ato (AccountTakeover)
| # | after | event | actor | new rule hits | score | band |
|---|---|---|---|---|---|---|
| 1 | +0m | PasswordReset | fraudster | R0 +10: First sighting on this Account: new device D-77, new country RO | 10 | Low |
| 2 | +4m | MfaEnrolled | fraudster | R1 +30: Password reset then MFA enrolment, 4 min apart; window 1h | 40 | Medium |
| 3 | +10m | EmailChanged | fraudster | R2 +20: Password reset then contact change, 10 min apart; window 24h | 60 | High |
| 4 | +1d5h | BankDetailsChanged | fraudster | R3 +30: Contact change then bank-detail change, 28h 50 min apart; window 72h<br>R7 +50: Password reset, contact change and bank-detail change from the same device, 29h apart; window 14d | 140 | Critical |
First reached High at step 3, EmailChanged (+10m); peak Critical.
```

This dry run gave only Margaret's file. Thus D-77 has no other Accounts, and the graph Rules do not agree.
Give the full folder to include the other Accounts:

```bash
uv run faa scenario dry-run packs/investor_portal/scenarios --pack packs/investor_portal
```

The dry run then gives the same result as the API: 55, 85, 105, 185.
It replays the Scenarios through an in-memory Link Graph, in the same way as the API.

To try a different Rules Version, add `--rules`. With v2, R7 does not exist:

```text
$ uv run faa scenario dry-run packs/investor_portal/scenarios --pack packs/investor_portal --rules v2
...
## margaret-ato (AccountTakeover)
| # | after | event | actor | new rule hits | score | band |
|---|---|---|---|---|---|---|
| 1 | +0m | PasswordReset | fraudster | R0 +10: First sighting on this Account: new device D-77, new country RO<br>G2 +25: Shares device D-77 with Confirmed Fraud Account ACC-PRIYA<br>G3 +20: device D-77 is shared by 4 Accounts; Ring threshold 3 | 55 | Medium |
| 2 | +4m | MfaEnrolled | fraudster | R1 +30: Password reset then MFA enrolment, 4 min apart; window 1h | 85 | High |
| 3 | +10m | EmailChanged | fraudster | R2 +20: Password reset then contact change, 10 min apart; window 24h | 105 | Critical |
| 4 | +1d5h | BankDetailsChanged | fraudster | R3 +30: Contact change then bank-detail change, 28h 50 min apart; window 72h | 135 | Critical |
First reached High at step 2, MfaEnrolled (+4m); peak Critical.
```

## 8. Examine the Link Graph

### 8.1 Get the Neighbourhood of an Account

The Link Graph is the table `identifier_links` in the same database as the events.
It has one row for each Account and each Linking Identifier.

```bash
curl -H "$A" http://localhost:8000/v1/accounts/ACC-MARGARET/neighbourhood
```

This is the result, with two identifiers removed so that it is shorter:

```json
[
    {
        "kind": "bank_account",
        "label": "Bank account",
        "value": "BA-9f31",
        "link_event_id": "a9a4d642-72c9-5dbe-a681-1f1bee3fbb8b",
        "others": []
    },
    {
        "kind": "device",
        "label": "Device",
        "value": "D-77",
        "link_event_id": "fab06833-3d08-5566-a52f-e1a2890f5e5a",
        "others": [
            {"account_id": "ACC-DEREK", "link_event_id": "a7d6bf87-210a-56b1-8df7-17256348210e", "confirmed_fraud": false},
            {"account_id": "ACC-PRIYA", "link_event_id": "b56f13c0-2bad-531a-8a4e-83558ed188cd", "confirmed_fraud": true},
            {"account_id": "ACC-LENA", "link_event_id": "8df549a6-d779-598d-ae3d-0d2b8d3bfe77", "confirmed_fraud": false}
        ]
    }
]
```

| Field | Meaning |
|---|---|
| `kind` and `value` | The identifier |
| `link_event_id` | The first event in which this Account used the identifier |
| `others` | The other Accounts that use the identifier, and if each one is a Confirmed Fraud |

An identifier with no other Account is in the result too.
For example, "the new bank account is used by no other Account" is also evidence.

### 8.2 Know the Linking Identifiers

`pack.yaml` tells which identifiers link Accounts:

```yaml
identifiers:
  - { kind: device, field: device_id, label: Device, linking: true }
  - { kind: email, field: attributes.new_email, label: Email, linking: true }
  - { kind: phone, field: attributes.new_phone, label: Phone, linking: true }
  - { kind: bank_account, field: attributes.new_bank_account_hash, label: Bank account, linking: true }
  - { kind: ip_address, field: ip_address, label: IP address, linking: false }
  - { kind: address, field: attributes.new_postcode, label: Address, linking: false }
```

IP addresses and postal addresses never link Accounts.
Many families, offices and mobile networks share them.
The service still masks them before an LLM sees them.

### 8.3 Know the graph Rules of the investor portal

| Rule | Points | Agrees when |
|---|---|---|
| G1 Shared bank account | 30 | The Account pays to a bank account that the Account of a different Holder also pays to |
| G2 Known-fraud proximity | 25 | The Account shares a device, an email, a phone or a bank account with a Confirmed Fraud Account |
| G3 Ring indicator | 20 | At least 3 Accounts, this one included, share one device, email, phone or bank account |

A graph Rule Hit has the time of the Link that completed the pattern.
Thus a hit that the service finds later goes into the correct Alert.

## 9. Follow the Case and the Investigation

### 9.1 Find the Case

When Margaret's Alert reached High, the service opened a Case.
A background worker started the Investigation immediately.

```bash
curl -H "$A" "http://localhost:8000/v1/cases?status=AwaitingApproval"
```

```json
[
    {
        "case_id": "10a947c9-8ffa-4947-90c9-e1246a756ef1",
        "alert_id": "242327cc-2d46-4969-9a9d-024a9ccfa591",
        "account_id": "ACC-MARGARET",
        "status": "AwaitingApproval",
        "band": "Critical",
        "score": 185,
        "opened_at": "2026-10-25T10:42:29.754919Z",
        "not_investigated": false,
        "investigation_failed": false,
        "review_flagged": false
    }
]
```

The result has four Cases: Margaret, Priya, Derek and Lena. The example shows only Margaret's Case.
The list shows the worst Cases first: by band, then by Score, then by the time that the Case opened.

A Case has these statuses:

| Status | Meaning |
|---|---|
| `Open` | The Case waits for the worker. If `not_investigated` is `true`, the budget guard stopped the Investigation. |
| `Investigating` | The agents work on the Case |
| `AwaitingApproval` | The Finding is ready. The Case waits for an analyst. |
| `Deciding` | The service applies a decision now. This status lasts less than one second. |
| `Closed` | An analyst recorded a decision |

### 9.2 Know what the Investigation does

The Investigation is a LangGraph workflow. This is how `src/fraud_audit_assist/agents/workflow.py` connects the steps:

```python
graph.add_edge(START, "enrich")
graph.add_edge("enrich", "investigate")
graph.add_conditional_edges("investigate", after_investigate, ["retry", "finalize", "validate"])
graph.add_conditional_edges("validate", after_check, ["review", "retry", "finalize"])
graph.add_conditional_edges("review", after_check, ["finalize", "retry"])
graph.add_edge("retry", "investigate")
graph.add_edge("finalize", "await_approval")
graph.add_edge("await_approval", "act")
graph.add_edge("act", END)
```

| Step | Who | What |
|---|---|---|
| `enrich` | Code | Makes a short report (the brief) about the Alert. It replaces every identifier with a Pseudonym. |
| `investigate` | Investigator agent | Uses three tools and writes the Finding |
| `validate` | Code | Examines the Finding. [Section 9.5](#95-know-the-guardrails) gives the checks. |
| `review` | Reviewer agent | Compares each claim with the evidence that the Finding cites |
| `retry` | Code | Sends the reasons back to the Investigator. This occurs one time at most. |
| `finalize` | Code | Decides the outcome: `approved`, `flagged` or `failed` |
| `await_approval` | The workflow | Stops. Keeps its state in the checkpoint store. Waits for the analyst. |
| `act` | Code | Records the decision and the Intervention |

The Investigator has three tools. Each tool can read only the Account of this Case:

| Tool | Gives |
|---|---|
| `get_account_timeline(days)` | The events of the Account in the last `days` days, 1 to 90, and at most 200 events |
| `query_graph_neighbourhood()` | Each Linking Identifier, the other Accounts that use it, and if they are Confirmed Fraud |
| `get_customer_baseline()` | The usual countries, devices, channels and activity, from before the suspicious events |

The tools have no Account parameter. Thus the model cannot ask about a different customer.

### 9.3 Know the budget guard

The worker examines the budget before each Investigation.
This is the code in `src/fraud_audit_assist/services/investigations.py`:

```python
if not self._settings.enabled or await cases.started_since(day_start) >= self._settings.daily_limit:
    # Skipped on purpose and never re-queued: re-queuing would spend the budget on a replay after a restart.
    case.not_investigated = True
```

- If `FAA_INVESTIGATION__ENABLED` is `false`, the worker does not investigate. The Case stays `Open` with `not_investigated: true`.
- If the number of Investigations on this UTC day reaches `FAA_INVESTIGATION__DAILY_LIMIT`, a new Case stays `Open` with `not_investigated: true`.
- The worker does not try that Case again later. An analyst can examine it without a Finding.

With `FAA_INVESTIGATION__DAILY_LIMIT=0`, the same Scenarios give this result:

```text
ACC-MARGARET Open True
ACC-PRIYA Open True
ACC-DEREK Open True
ACC-LENA Open True
```

The worker logs each Case that it does not investigate:

```json
{"case_id": "2e5cd95f-5864-4512-b2f5-3515247f5660", "enabled": true, "event": "investigation skipped by budget guard", "level": "warning", "logger": "fraud_audit_assist.services.investigations", "timestamp": "2026-10-25T10:45:08.662090Z"}
```

### 9.4 Know what the model sees

The model never sees a real identifier.
This is the brief that the Investigator received for Margaret's Case:

```text
Case for Account ACCOUNT-3.
Alert Score: 185. Risk Band: Critical. Rules version: v3.
Rule Hits:
- R0 (+10) First sighting on this Account: new device DEVICE-1, new country RO. Evidence: E-3
- G2 (+25) Shares device DEVICE-1 with Confirmed Fraud Account ACCOUNT-2. Evidence: E-2, E-3
- G3 (+20) device DEVICE-1 is shared by 4 Accounts; Ring threshold 3. Evidence: E-1, E-2, E-3
- R1 (+30) Password reset then MFA enrolment, 4 min apart; window 1h. Evidence: E-3, E-4
- R2 (+20) Password reset then contact change, 10 min apart; window 24h. Evidence: E-3, E-5
- R3 (+30) Contact change then bank-detail change, 28h 50 min apart; window 72h. Evidence: E-5, E-6
- R7 (+50) Password reset, contact change and bank-detail change from the same device, 29h apart; window 14d. Evidence: E-3, E-5, E-6
Evidence events:
E-1 2026-09-01T20:00:00Z PasswordReset account ACCOUNT-1 customer CUSTOMER-1 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web
E-2 2026-09-07T13:00:00Z PasswordReset account ACCOUNT-2 customer CUSTOMER-2 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web
E-3 2026-09-21T09:00:00Z PasswordReset account ACCOUNT-3 customer CUSTOMER-3 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web
E-4 2026-09-21T09:04:00Z MfaEnrolled account ACCOUNT-3 customer CUSTOMER-3 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web
E-5 2026-09-21T09:10:00Z EmailChanged account ACCOUNT-3 customer CUSTOMER-3 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web new_email=EMAIL-1
E-6 2026-09-22T14:00:00Z BankDetailsChanged account ACCOUNT-3 customer CUSTOMER-3 device DEVICE-1 ip IP_ADDRESS-1 country RO channel web new_bank_account_hash=BANK_ACCOUNT-1
```

The graph tool gave these facts:

```text
G-1 Bank account BANK_ACCOUNT-1 is used by no other Account
G-2 Device DEVICE-1 is also used by ACCOUNT-1, ACCOUNT-2 (Confirmed Fraud), ACCOUNT-4
G-3 Device DEVICE-2 is used by no other Account
G-4 Email EMAIL-1 is used by no other Account
```

`ACC-MARGARET` became `ACCOUNT-3`. `D-77` became `DEVICE-1`. `ACC-PRIYA` became `ACCOUNT-2`.
The numbers start again in each Investigation, so one device has different Pseudonyms in different Cases.
The map from Pseudonym to real value stays on the Case. Only the analyst sees the real values.

Each event has an Evidence Ref, for example `E-3`. Each graph fact has a ref, for example `G-2`.
The Finding must cite these refs.

The instructions of the agents are in `src/fraud_audit_assist/agents/prompts/`.
The service fills in the description, the typologies and the actions of the pack.
The name of the prompt file, for example `investigator.v1`, is the Prompt Version. Every Case and Audit Entry records it.

### 9.5 Know the guardrails

Before the Reviewer starts, code examines the Finding:

- The verdict is a typology of the pack, `Inconclusive` or `LikelyGenuine`.
- The recommendation is an action of the pack.
- The summary has 1 to 120 words.
- The Finding cites at least one ref, and each cited ref exists in this Investigation.
- A restrictive action needs a fraud verdict.

The last check is in `src/fraud_audit_assist/agents/validation.py`:

```python
action = actions.get(finding.recommendation)
fraud_verdicts = {t.id for t in pack.typologies}
if action is not None and action.restrictive and finding.verdict not in fraud_verdicts:
    reasons.append(
        f"{action.id} is restrictive and needs a fraud verdict; with {finding.verdict} recommend a non-restrictive action."
    )
```

A restrictive action limits the customer, for example `BlockBankChanges`.
An agent that thinks a customer is genuine cannot recommend to block that customer.

If a check fails, the Investigator gets the reasons and writes the Finding one more time.
These rules also protect the prompts:

- The service masks every identifier with a Pseudonym.
- The service shows an attribute only if the pack lists it in `non_identifying_attributes`, for example `amount`.
- The service shows such a value only if it is one short word. Other text becomes `withheld`.
- All other attributes become `VALUE-n`. Thus text from a source system cannot give instructions to the model.

### 9.6 Read the Finding

```bash
curl -H "$A" http://localhost:8000/v1/cases/<case id>
```

This is the result with the fake provider, with some refs removed so that it is shorter:

```json
{
    "case_id": "10a947c9-8ffa-4947-90c9-e1246a756ef1",
    "account_id": "ACC-MARGARET",
    "status": "AwaitingApproval",
    "band": "Critical",
    "score": 185,
    "not_investigated": false,
    "investigation_failed": false,
    "review_flagged": false,
    "finding": {
        "verdict": "AccountTakeover",
        "confidence": "Medium",
        "summary": "Scripted Finding from the fake provider, not a real assessment: 6 flagged events, and a shared identifier with a Confirmed Fraud Account.",
        "recommendation": "BlockBankChanges",
        "evidence": {
            "E-3": ["fab06833-3d08-5566-a52f-e1a2890f5e5a"],
            "E-4": ["cc625467-11ae-548f-9748-ae1bbcb28dff"],
            "G-2": [
                "fab06833-3d08-5566-a52f-e1a2890f5e5a",
                "a7d6bf87-210a-56b1-8df7-17256348210e",
                "b56f13c0-2bad-531a-8a4e-83558ed188cd",
                "8df549a6-d779-598d-ae3d-0d2b8d3bfe77"
            ]
        }
    },
    "rejection_reasons": [],
    "failure": null,
    "prompt_versions": "investigator.v1+reviewer.v1",
    "usage": [
        {"agent": "investigator", "prompt_version": "investigator.v1", "model": "scripted-fake", "input_tokens": 1309, "output_tokens": 45, "cost": 0.0, "latency_ms": 0},
        {"agent": "investigator", "prompt_version": "investigator.v1", "model": "scripted-fake", "input_tokens": 1403, "output_tokens": 77, "cost": 0.0, "latency_ms": 0},
        {"agent": "reviewer", "prompt_version": "reviewer.v1", "model": "scripted-fake", "input_tokens": 796, "output_tokens": 8, "cost": 0.0, "latency_ms": 0}
    ],
    "trace_id": "2fc338a4befdfb906c2fb9228e47ac6c",
    "decision": null,
    "interventions": []
}
```

| Field | Meaning |
|---|---|
| `verdict` | A typology of the pack (here `AccountTakeover` or `RegistrationFraud`), `Inconclusive` or `LikelyGenuine` |
| `confidence` | `Low`, `Medium` or `High`. The Investigator gives this value. |
| `summary` | At most 120 words. You see real identifiers here. The model saw only Pseudonyms. |
| `recommendation` | An action of the pack: here `BlockBankChanges`, `ContactHolder` or `NoAction` |
| `evidence` | Each cited ref and the events behind it. Use them to examine each claim. |
| `review_flagged` | `true` if the Reviewer rejected the Finding two times. Read the Finding with more care. `rejection_reasons` tells you why. |
| `investigation_failed` | `true` if the agents could not write a Finding, for example because the LLM failed. `failure` tells you why. Examine the Case without a Finding. |
| `usage` | Each model call: the agent, the model, the tokens, the estimated cost and the time |

With a real LLM, the summary comes from the model, and it is different for each Case.

## 10. Record the Analyst Decision

### 10.1 Make sure that a restart does not lose a Case

Do this test with a Case in `AwaitingApproval`:

1. Stop the API with Ctrl+C.
2. Start the API again with `uv run faa serve`.
3. Get the Case again. The status is still `AwaitingApproval`.

The workflow keeps its state in the checkpoint store.
The service records a checkpoint after each step of the workflow.
If the API stops during an Investigation, the next start continues from the last step that completed.
The service does not pay again for the model calls of completed steps.

### 10.2 Send the decision

The analyst reads the Finding and the evidence. Then the analyst sends a decision:

```bash
curl -X POST -H "$A" -H "Content-Type: application/json" \
  http://localhost:8000/v1/cases/<case id>/decision \
  -d '{"outcome":"ConfirmedFraud","action":"BlockBankChanges","analyst_id":"analyst-7","notes":"Holder confirmed by phone it was not her."}'
```

| Field | Values |
|---|---|
| `outcome` | `ConfirmedFraud` or `FalsePositive` |
| `action` | An action of the pack: here `BlockBankChanges`, `ContactHolder` or `NoAction`. It can be different from the recommendation. |
| `analyst_id` | The name or ID of the analyst |
| `notes` | Free text, at most 4,000 characters |

The API returns HTTP 200 and the closed Case. The new parts of the Case are these:

```json
"status": "Closed",
"decision": {
    "outcome": "ConfirmedFraud",
    "action": "BlockBankChanges",
    "analyst_id": "analyst-7",
    "notes": "Holder confirmed by phone it was not her.",
    "decided_at": "2026-10-25T10:42:38.069808Z"
},
"interventions": [
    {
        "action": "BlockBankChanges",
        "until": "2026-11-01T10:42:38.069808Z",
        "created_at": "2026-10-25T10:42:38.069808Z"
    }
]
```

The Intervention lasts 7 days. The `duration_days` of the action in `pack.yaml` controls this:

```yaml
actions:
  - id: BlockBankChanges
    description: Block bank-detail changes and payouts to new bank details for a period.
    restrictive: true
    duration_days: 7
```

The service records the Intervention for the source system. The service does not stop later events itself.
The source system must read the Intervention and stop the bank change.

### 10.3 Know the errors

An action that the pack does not have gives HTTP 422:

```json
{"detail":"unknown action 'FreezeEverything'; allowed: BlockBankChanges, ContactHolder, NoAction"}
```

A Case that is not in `AwaitingApproval` gives HTTP 409, and the service changes nothing:

```text
HTTP/1.1 409 Conflict
{"detail":"case 10a947c9-8ffa-4947-90c9-e1246a756ef1 is Closed, not AwaitingApproval"}
```

If two analysts send a decision at the same time, only one decision is recorded. The other analyst gets HTTP 409.

### 10.4 Know what Act does

First, the service changes the Case from `AwaitingApproval` to `Deciding`.
Then it starts the workflow again from its checkpoint. No model call occurs.
The `act` step does these tasks in one transaction:

1. It records the Analyst Decision.
2. It records the Intervention, if the action has one. `NoAction` has no Intervention.
3. It writes two Audit Entries: `DecisionRecorded` and `ActionTaken`.
4. It closes the Case.
5. For `ConfirmedFraud`, it records the Account as a Confirmed Fraud. Then it scores the Neighbours of the Account again.

Thus, after this decision, an Account that shares a device with Margaret gets the G2 Rule Hit.

If a step fails, the service writes nothing, and the Case goes back to `AwaitingApproval`.
The analyst can send the decision again.

### 10.5 Read the audit trail

```bash
curl -H "$A" http://localhost:8000/v1/cases/<case id>/audit
```

The result has four Audit Entries:

| Kind | Actor | Records |
|---|---|---|
| `InvestigationCompleted` | `investigator` | Model, Prompt Version, Rules Version, cited refs with their events, tokens, cost |
| `ReviewCompleted` | `reviewer` | Model, Prompt Version, Rules Version, tokens, cost |
| `DecisionRecorded` | `analyst-7` | The outcome, the action, the notes, and if the analyst agreed with the recommendation |
| `ActionTaken` | `analyst-7` | The action and the end date of the Intervention |

This is the first entry, with the refs removed:

```json
{
    "kind": "InvestigationCompleted",
    "actor": "investigator",
    "model": "scripted-fake",
    "prompt_version": "investigator.v1",
    "rules_version": "v3",
    "evidence": {"cited": {"E-3": ["fab06833-3d08-5566-a52f-e1a2890f5e5a"]}},
    "input_tokens": 2712,
    "output_tokens": 122,
    "cost": 0.0,
    "details": {"outcome": "approved", "calls": 2, "failure": null},
    "created_at": "2026-10-25T10:42:29.984499Z"
}
```

This is the `DecisionRecorded` entry:

```json
{
    "kind": "DecisionRecorded",
    "actor": "analyst-7",
    "rules_version": "v3",
    "details": {
        "outcome": "ConfirmedFraud",
        "action": "BlockBankChanges",
        "notes": "Holder confirmed by phone it was not her.",
        "recommended": "BlockBankChanges",
        "agreed_with_recommendation": true
    },
    "created_at": "2026-10-25T10:42:38.069808Z"
}
```

The service never changes an Audit Entry after it writes it.

## 11. Monitor the service

### 11.1 See the logs

The service writes one JSON line for each log event, to standard output.
Each line written for a request has the `request_id` of that request.
The API returns the same value in the `X-Request-ID` header.
If a client sends an `X-Request-ID` header, the service uses that value.

These log events are the most useful:

| Event | Meaning |
|---|---|
| `batch ingested` | A batch was stored. It gives the numbers of new events, duplicates, re-scored Accounts and new Cases. |
| `case opened` | An Alert reached High |
| `llm call` | One model call: agent, model, Prompt Version, time, attempts, tokens and estimated cost |
| `llm call failed` | One model call failed. `fallback_next` tells you if the fallback model is next. |
| `investigation awaiting approval` | An Investigation completed |
| `investigation skipped by budget guard` | The budget stopped an Investigation |
| `investigation failed to run` | An infrastructure error stopped an Investigation. The next start tries it again. |
| `confirmed fraud recorded` | An Account became a Confirmed Fraud |

This is an `llm call` line:

```json
{"agent": "investigator", "model": "scripted-fake", "prompt_version": "investigator.v1", "latency_ms": 2, "attempts": 1, "input_tokens": 1196, "output_tokens": 45, "cost": 0.0, "tool_calls": 2, "event": "llm call", "level": "info", "logger": "fraud_audit_assist.agents.llm.gateway", "timestamp": "2026-10-25T10:42:29.616053Z"}
```

The service never logs a key or a password.

### 11.2 See the metrics

```bash
curl http://localhost:8000/metrics
```

After you send the Scenarios, the result includes these lines:

```text
faa_events_ingested_total{outcome="ingested"} 79.0
faa_events_ingested_total{outcome="duplicate"} 0.0
faa_rule_hits_total{rule_id="R0"} 4.0
faa_rule_hits_total{rule_id="G3"} 4.0
faa_cases_opened_total 4.0
faa_llm_tokens_total{agent="investigator",direction="input"} 9927.0
faa_llm_tokens_total{agent="investigator",direction="output"} 461.0
faa_llm_calls_total{agent="investigator",outcome="ok"} 8.0
faa_llm_calls_total{agent="reviewer",outcome="ok"} 4.0
faa_investigations_total{outcome="approved"} 4.0
```

| Metric | What |
|---|---|
| `faa_events_ingested_total` | Events, by outcome: `ingested` or `duplicate` |
| `faa_rule_hits_total` | Rule Hits, by Rule |
| `faa_alert_band_changes_total` | Alerts that reached a band, by band |
| `faa_cases_opened_total` | Cases |
| `faa_ingest_seconds` | The time of each ingest request |
| `faa_llm_calls_total` | Model calls, by agent and outcome |
| `faa_llm_tokens_total` | Tokens, by agent and direction |
| `faa_llm_cost_total` | Estimated cost, by agent |
| `faa_llm_seconds` | The time of each model call |
| `faa_investigations_total` | Investigations, by outcome: `approved`, `flagged` or `failed` |
| `faa_investigation_seconds` | The time from the start of the Investigation to `await_approval` |

The counters start at 0 when the process starts. A Prometheus server keeps the history.
With the fake provider, the cost is 0.

### 11.3 See the traces

The service makes OpenTelemetry spans for each request and for these steps:

| Span | What |
|---|---|
| `ingest` | One batch |
| `detect` | The scoring of one Account |
| `investigate case` | One Investigation |
| `chat <agent>` | One model call, with the model and the tokens |
| `decide case` | One decision |

The service sends the spans only if you set an OpenTelemetry collector:

```bash
export FAA_OTEL_EXPORTER_OTLP_ENDPOINT=http://localhost:4318
```

The collector must accept OTLP over HTTP. Jaeger, Grafana Tempo and most cloud monitoring services accept it.
Each Case keeps the `trace_id` of its Investigation, so that you can find the trace from the Case.

## 12. Operate with a real LLM

**PAID STEP.** Each Investigation in this section costs money.

Do this section only after [section 3](#3-set-up-an-llm-provider-optional) and [section 4.5](#45-configure-the-service-for-a-paid-provider).

CAUTION: Each new Case now costs money.
Do not send a large Dataset to the API with a real LLM.
Before you send many events, set `FAA_INVESTIGATION__ENABLED=false`.

1. Stop the API with Ctrl+C.
2. Make sure that `.env` has the provider, the model, the key and the prices.
3. Start the API again. If a setting is missing, the API stops and tells you the setting.
4. Make sure that the `service started` log line shows your provider and model.
5. Send one new Scenario. Use a copy of Margaret's file with a new `name` and a new `account_id`.
   The service ignores events that it has already, so the old file does not open a new Case.
6. Wait until the Case is in `AwaitingApproval`.
7. Read the Finding. The summary now comes from the model.
8. Read the `llm call` log lines. Each line shows the tokens and the estimated cost of one call.
9. Look at `faa_llm_cost_total` in the metrics.
10. After some hours, compare the estimate with the cost view of your provider.

### 12.1 Know how the service handles model errors

Every model call goes through `LlmGateway` in `src/fraud_audit_assist/agents/llm/gateway.py`:

| Error | What the service does |
|---|---|
| Timeout, lost connection, rate limit (429), server error (5xx) | It tries again, at most `FAA_LLM__MAX_RETRIES` times, with a longer wait each time |
| Bad request, wrong key (4xx) | It does not try again |
| All attempts fail | It uses the fallback model, if you set one |
| The fallback also fails | The Investigation fails. The Case goes to the analyst with `investigation_failed: true`. |

The Rules and the Alerts do not need the LLM. They continue to work when the LLM fails.

To go back to the free provider, set `FAA_LLM__PROVIDER=fake` and start the API again.

## 13. Use a different Domain Pack

### 13.1 Start the API with the digital banking pack

1. Stop the API.
2. Start it with the other pack:

   ```bash
   FAA_PACK_DIR=packs/digital_banking uv run faa serve
   ```

3. Make sure that the service uses the pack:

   ```bash
   curl http://localhost:8000/health/ready
   ```

   ```json
   {"status":"ok","pack":"digital_banking","rules_version":"v1"}
   ```

CAUTION: One database is for one pack. Use a new database when you change the pack.
For example, set `FAA_DATABASE_URL=sqlite+aiosqlite:///./banking.db`.

### 13.2 Send the digital banking Scenarios

```bash
uv run faa scenario post packs/digital_banking/scenarios --pack packs/digital_banking
```

```text
tom-mule-victim: 18 ingested, 0 already ingested
tom-mule-victim: Confirmed Fraud seeded
aisha-sim-swap: 19 ingested, 0 already ingested
nina-new-landlord: 15 ingested, 0 already ingested
ghost-application: 3 ingested, 0 already ingested
```

Three Cases open:

```text
ACC-AISHA Critical 180
ACC-TOM Critical 135
ACC-GHOST High 75
```

Aisha's dry run shows how the Score increases:

```text
## aisha-sim-swap (AccountTakeover)
| # | after | event | actor | new rule hits | score | band |
|---|---|---|---|---|---|---|
| 1 | +0m | SimSwapDetected | holder | - | 0 | Low |
| 2 | +25m | PasswordReset | fraudster | B0 +10: First sighting on this Account: new device D-GANG-2<br>B1 +35: SIM swap reported then account recovery, 25 min apart; window 24h | 45 | Medium |
| 3 | +28m | DeviceRegistered | fraudster | B2 +30: Password reset then new device or MFA binding, 3 min apart; window 1h | 75 | High |
| 4 | +40m | PhoneChanged | fraudster | B4 +20: Account recovery then contact change, 12 min apart; window 24h | 95 | Critical |
| 5 | +1h5m | PayeeAdded | fraudster | G1 +25: Pays payee account PA-MULE-77, which ACC-TOM of a different Customer also pays<br>G2 +25: Shares payee account PA-MULE-77 with Confirmed Fraud Account ACC-TOM | 145 | Critical |
| 6 | +1h12m | TransferInitiated | fraudster | B3 +35: New payee then a payment of at least 1,000 to it, 7 min apart; window 24h | 180 | Critical |
First reached High at step 3, DeviceRegistered (+28m); peak Critical.
```

The Case reached High before the money moved.
Aisha's Finding uses the typologies and the actions of this pack:

| Field | Value |
|---|---|
| `verdict` | `AccountTakeover` |
| `confidence` | `Medium` |
| `recommendation` | `HoldOutboundPayments` |
| `evidence` | `E-1` to `E-7`, and `G-3` (the payee that Confirmed Fraud ACC-TOM also pays) |

The analyst actions are now `HoldOutboundPayments`, `StepUpVerification`, `ContactCustomer` and `NoAction`.

### 13.3 Know that the pack controls the event types

The service refuses an event type that the pack does not have.
For example, `SaleRequested` is an investor portal event:

```json
{"detail":"unknown event types for pack digital_banking: ['SaleRequested']"}
```

### 13.4 Make a new Domain Pack

A new business is a new folder in `packs/`. It needs no code.
[domain-packs.md](domain-packs.md) tells you how to write each file.

1. Copy a pack: `cp -r packs/digital_banking packs/<your_pack>`.
2. In `pack.yaml`, change `id` to the folder name.
3. Change the event types, the identifiers, the typologies and the actions.
4. Write the Rules in `rules/v1.yaml`.
5. Write some Scenarios in `scenarios/`.
6. Do a dry run:

   ```bash
   uv run faa scenario dry-run packs/<your_pack>/scenarios --pack packs/<your_pack>
   ```

7. Do the tests: `uv run pytest tests/unit/domain`.

If a file is not correct, the dry run and the service stop, and they tell you the problem.

## 14. Make synthetic data

### 14.1 Make a Dataset

A Dataset is a file of generated Accounts. Each Account has its events and its Label.
The Personas in `personas.yaml` of the pack control the behaviour.

```bash
uv run faa dataset generate --pack packs/investor_portal --seed 42 --accounts 1000
```

```text
Wrote datasets/investor_portal-seed-42-1000.jsonl: 1000 accounts, 34 fraud, 18852 events
```

The same pack, seed and number of Accounts always give the same file, byte for byte.

| Option | Default | Use |
|---|---|---|
| `--pack` | `FAA_PACK_DIR` | The pack |
| `--seed` | `42` | The seed of the random numbers |
| `--accounts` | `1000` | The number of Accounts |
| `--out` | `datasets/<pack>-seed-<seed>-<accounts>.jsonl` | The output file |

Each line of the file is one Account:

```json
{"account_id":"INV-ACC-00001","label":{"persona":"quiet_holder","kind":"Genuine","tags":[]},"events":[{"event_id":"44eb9d95-8f0f-586d-adea-e8b5ec463a29","account_id":"INV-ACC-00001","customer_id":"INV-CUST-00001","type":"LoginSucceeded","occurred_at":"2026-07-02T03:01:00Z","device_id":"D-00001","ip_address":"2001:db8::1","country":"GB","channel":"web","attributes":{}}, ...]}
```

`.gitignore` excludes the `datasets/` folder. Make the file again when you need it.

### 14.2 Send a Dataset to the API

**CAUTION:** Each Case that the Dataset opens starts an Investigation.
With a real LLM, this costs money. Set `FAA_INVESTIGATION__ENABLED=false` first, or use the fake provider.

```bash
uv run faa dataset post datasets/investor_portal-seed-42-1000.jsonl --api-key ingest-key-local
```

The command sends the events without their Labels, in sequence of event time, in batches of 200.
Thus the Link Graph grows in the same sequence as in a live system.

The API must use the same pack as the Dataset.

## 15. Change a Rule safely

The service never changes its own Rules. A person does these steps:

1. Copy the current Rules file to a new version:

   ```bash
   cp packs/investor_portal/rules/v3.yaml packs/investor_portal/rules/v4.yaml
   ```

2. Open `v4.yaml`.
3. Change `version` to `v4`.
4. Change the Rule. For example, change the points of R2 from `20` to `10`.
5. Do a dry run with the new version and with the current version:

   ```bash
   uv run faa scenario dry-run packs/investor_portal/scenarios --pack packs/investor_portal --rules v3
   uv run faa scenario dry-run packs/investor_portal/scenarios --pack packs/investor_portal --rules v4
   ```

6. Compare the two results. Look at the step where each fraud Account first reaches High.
7. Compare the change with the golden dataset ([section 16](#16-use-the-golden-dataset)).
8. If the new version is better, deploy it. Use one of these two methods:
   - Set `FAA_RULES_VERSION=v4`.
   - Change `default_rules` in `pack.yaml` to `v4`.
9. Start the API again. `GET /v1/rules` now shows `"version": "v4"`.
10. Commit the new Rules file and the change together.

Each new Alert records `"rules_version": "v4"`.
Old Alerts keep their version. The service never scores them again with the new Rules.

CAUTION: Do not change a Rules file that has scored Alerts. Always make a new version.
If you change an old file, nobody can show how an old Alert got its Score.

Note: The API reads the Rules file only when it starts.
If the file is not correct, the API does not start, and it shows the error.

## 16. Use the golden dataset

The golden dataset is in `evals/golden/`.
It has 26 hand-written cases: 16 for the investor portal and 10 for digital banking.
Each case gives an Account journey and the correct result.

| Part of a case | What it gives |
|---|---|
| `target` and `context` | The journey to judge, and other Accounts that must be in the Link Graph first |
| `label` | The ground truth: a typology or `Genuine` |
| `expected.detection` | The exact result of the Rules: the peak band, the Score, the step that first reached High, and the Rules that agree |
| `expected.investigation` | The range of correct Findings: the verdicts and recommendations that are acceptable, and the evidence that the Finding must cite |

Some cases are known misses, with the tag `known-miss`.
They show fraud that the current Rules do not find. They stay in the dataset on purpose.

[evals/golden/README.md](../evals/golden/README.md) describes each field.

Note: The program that scores the service against the golden dataset is future work (ADR-0016).
Until then, use the dataset to review a Rule change by hand, case by case.

## 17. Deploy to production

### 17.1 Build the image

```bash
docker build -t fraud-audit-assist:<version> .
```

The image runs as a user that is not root (user ID 10001).
It contains the service, the Domain Packs and the migrations. It does not contain the tests or the documentation.

### 17.2 Set the production settings

Give these settings as environment variables or from a secret store. Do not put them in the image.

```bash
FAA_ENVIRONMENT=production
FAA_DATABASE_URL=postgresql+psycopg://<user>:<password>@<host>:5432/<database>
FAA_PACK_DIR=/app/packs/investor_portal
FAA_INGEST_API_KEY=<a long random value>
FAA_ANALYST_API_KEY=<a different long random value>
FAA_LLM__PROVIDER=<openai | azure_openai | anthropic>
FAA_LLM__MODEL=<model or deployment>
FAA_LLM__API_KEY=<key>
FAA_LLM__INPUT_PRICE_PER_1K=<price>
FAA_LLM__OUTPUT_PRICE_PER_1K=<price>
```

To make a long random key, do this command:

```bash
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 17.3 Migrate the database

Do this step before the new version gets traffic. Use the same image and the same settings:

```bash
docker run --rm --env-file prod.env fraud-audit-assist:<version> faa db upgrade
```

```text
Database migrated to head: <host>:5432/<database>
```

In production, the API does not make or change tables when it starts. Only `faa db upgrade` changes the schema.

### 17.4 Start the API

1. Start **one** container of the image. The default command is `faa serve --host 0.0.0.0 --port 8000`.
2. Use `/health/live` as the liveness probe.
3. Use `/health/ready` as the readiness probe.
4. Let your Prometheus server read `/metrics`.

CAUTION: Start only one instance.
The Investigation queue is in the memory of the process. Two instances can investigate one Case two times.
[architecture.md](architecture.md#known-limits) tells you more.

## 18. Reset and recover

The [runbook](runbook.md) has the recovery procedures. Use it for these tasks:

| Task | Runbook section |
|---|---|
| Delete all data and start again | [Reset data](runbook.md#reset-data-development) |
| Build the Link Graph again from the events | [Reset data](runbook.md#reset-data-development), "Link Graph only" |
| Stop spending money on the LLM | [Spend control](runbook.md#spend-control) |
| Find the fix for an error | [Common failures](runbook.md#common-failures) |

To build the Link Graph again, stop the API and do this command:

```bash
uv run faa db rebuild-links
```

```text
Rebuilt links from 79 events.
```

The command builds the Links from the events. It does not score the Alerts again, because Alerts never change.

To run the tests, read the [README](../README.md#tests-and-checks).
