# ADR-0003: Python, FastAPI and LangGraph

Status: Accepted | Date: 2026-10-24

The stack:

| Concern | Choice |
|---|---|
| API | FastAPI + uvicorn |
| Persistence | SQLAlchemy 2 (async) + Alembic; PostgreSQL in production, SQLite for development |
| Agents | LangGraph `StateGraph` with `interrupt()` for the human step and a checkpointer for durability |
| LLM | LangChain chat models (OpenAI, Azure OpenAI, Anthropic) behind one gateway, plus a free scripted fake |
| Config | pydantic-settings, validated at startup |
| Observability | structlog JSON logs, OpenTelemetry traces, Prometheus metrics |
| Tooling | uv (lock file), ruff, mypy strict, pytest |

LangGraph gives the shape a regulated process needs: fixed steps with agents inside them, a pause that cannot be
skipped and a checkpoint that survives a restart. Its checkpointers already support PostgreSQL and SQLite, so no
custom store is needed.

## Considered options

- **LangGraph prebuilt ReAct agent for the Investigator**: less code, but the tool loop is where the budget and
  masking are enforced, so it is written out (about 30 lines) and bounded explicitly.
- **CrewAI / AutoGen**: more autonomous multi-agent styles; a fixed workflow is what a regulated process needs.
