# fraud-audit-assist

Rules-first fraud detection, LangGraph investigation agents, a human analyst decides. Python counterpart of vigil,
made generic through Domain Packs. GLOSSARY.md is the domain language (use its terms in code and docs);
docs/architecture.md explains the modules; docs/adr/ records decisions (numbering follows vigil's where a decision
carries over).

## Working agreement

- Business specifics belong in `packs/<pack>/`, never in code. If a change needs a pack-specific `if`, extend the
  pack schema instead.
- `detection/`, `graph/` and `agents/` stay free of database and web imports; they depend on `domain/` and on the
  protocols `LinkGraphReader`, `InvestigationSource` and `ActHandler`.
- Tests first for detection logic. Tests never call a real LLM: use `ScriptedChatModel`.
- Every model call goes through `LlmGateway`; every string shown to a model goes through `EvidenceLedger`.
- Never edit a rules file that has scored Alerts: add `vN+1.yaml`.
- Write a short ADR when a decision changes behaviour or structure.
- Comments explain why, not what. Docstrings state the contract of public functions.

## Commands

- `uv run ruff check . && uv run ruff format --check . && uv run mypy && uv run pytest`
- `FAA_TEST_POSTGRES_URL=postgresql+psycopg://... uv run pytest` also runs the database tests on PostgreSQL.
- `uv run faa scenario dry-run packs/<pack>/scenarios --pack packs/<pack>` to see what a rule change does.
- New migration: change `persistence/tables.py`, then
  `uv run alembic -x url=sqlite+aiosqlite:///./scratch.db revision --autogenerate -m "<what>"`; review it (use
  `sa.DateTime(timezone=True)`, not the app's type) and keep `tests/integration/test_migrations.py` green.

## Gotchas

- LangGraph node names (`enrich`, `investigate`, ..., `await_approval`, `act`) are part of stored checkpoints:
  renaming one strands Cases that are waiting.
- `await_approval` re-runs from the top on resume, so nothing may happen before `interrupt()`.
- SQLite is single-writer: writes are serialised in-process, and the checkpointer uses a separate `.checkpoints` file.
- `evals/golden/*.jsonl` expectations were computed by replay: a rule change that alters one is a behaviour change.
