# ADR-0005: The Investigation is a LangGraph workflow, checkpointed at every step

Status: Accepted | Date: 2026-10-24

Each Case's Investigation is one LangGraph thread (thread id = CaseId):

    enrich -> investigate -> validate -> review -> finalize -> await_approval -> act
                  ^             |           |
                  +---- retry <-+-----------+

Code does everything code can do: enrich builds the masked brief, validate checks the Finding, finalize decides the
outcome. Agents only investigate and review. `await_approval` calls `interrupt()` with the masked outcome; the
thread pauses until `POST /v1/cases/{id}/decision` resumes it with `Command(resume=decision)`, and `act` records it.

The state is plain JSON, the Evidence Ledger (Pseudonyms and refs) included. So every step is checkpointed, and the
runner can resume a thread that a crash interrupted mid-Investigation from its last checkpoint instead of paying for
finished agent calls again. Checkpoints go to PostgreSQL (`langgraph-checkpoint-postgres`, through a connection pool)
or to a SQLite file beside the development database.

Rules the code relies on:
- `await_approval` re-runs from the top on resume, so it does nothing before `interrupt()`.
- Nodes never raise on an expected failure: LLM errors and unreadable replies become an InvestigationFailed outcome,
  so every path reaches the analyst. Infrastructure errors do raise, and the startup re-queue retries the Case.
- Node names are part of the checkpoint; renaming one strands threads that are waiting.

## Why the ledger lives in the state

The masking objects could have been passed between steps by reference, but then nothing before Await Approval could
be checkpointed and a crash would restart the Investigation from the beginning. Keeping the ledger in the state means
any step can resume.

## Considered options

- **A hand-written state machine over Case columns**: fewer moving parts, but it would reimplement pause, resume and
  checkpointing that LangGraph provides and tests.
