"""Composition root: builds every service once, from settings, and hands them to the API or the CLI."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass
from datetime import datetime

from langchain_core.language_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from fraud_audit_assist.agents.agents import Investigator, Reviewer
from fraud_audit_assist.agents.llm.factory import build_chat_model
from fraud_audit_assist.agents.llm.gateway import LlmGateway, ModelEndpoint
from fraud_audit_assist.agents.prompts import load_prompt
from fraud_audit_assist.agents.source import SqlInvestigationSource
from fraud_audit_assist.agents.workflow import WorkflowDeps, build_investigation_graph
from fraud_audit_assist.config import Settings
from fraud_audit_assist.domain.packs import LoadedPack, load_pack
from fraud_audit_assist.observability.metrics import Metrics
from fraud_audit_assist.persistence.db import Database
from fraud_audit_assist.services.case_queue import InProcessCaseQueue
from fraud_audit_assist.services.ingestion import IngestionService
from fraud_audit_assist.services.investigations import (
    ActService,
    DecisionService,
    InvestigationRunner,
    InvestigationWorker,
    WorkflowGraph,
    utc_now,
)

INVESTIGATOR_PROMPT = "investigator.v1"
REVIEWER_PROMPT = "reviewer.v1"


@dataclass(frozen=True)
class Container:
    settings: Settings
    pack: LoadedPack
    db: Database
    metrics: Metrics
    queue: InProcessCaseQueue
    ingestion: IngestionService
    graph: WorkflowGraph
    runner: InvestigationRunner
    worker: InvestigationWorker
    decisions: DecisionService
    model_name: str


@asynccontextmanager
async def build_container(
    settings: Settings,
    *,
    chat_model: BaseChatModel | None = None,
    checkpointer: BaseCheckpointSaver[str] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> AsyncIterator[Container]:
    """Builds the services. `chat_model` and `checkpointer` let tests swap in a scripted model and memory."""
    pack = load_pack(settings.pack_dir, settings.rules_version)
    db = Database(settings.database_url)
    metrics = Metrics()
    queue = InProcessCaseQueue(settings.investigation.queue_capacity)
    ingestion = IngestionService(db, pack, queue, metrics, clock)

    llm = settings.llm
    model_name = "scripted-fake" if llm.provider == "fake" and chat_model is None else llm.model
    primary = ModelEndpoint(
        model_name, chat_model or build_chat_model(llm, llm.provider, llm.model, llm.api_key)
    )
    fallback = None
    if llm.fallback_provider and llm.fallback_model:
        fallback = ModelEndpoint(
            llm.fallback_model,
            build_chat_model(llm, llm.fallback_provider, llm.fallback_model, llm.fallback_api_key),
        )
    gateway = LlmGateway(primary, llm, metrics, fallback)
    deps = WorkflowDeps(
        pack=pack.pack,
        source=SqlInvestigationSource(db, pack.pack),
        investigator=Investigator(gateway, load_prompt(INVESTIGATOR_PROMPT, pack.pack), llm.max_tool_rounds),
        reviewer=Reviewer(gateway, load_prompt(REVIEWER_PROMPT, pack.pack)),
        act=ActService(db, pack, ingestion, queue, clock),
        max_attempts=settings.investigation.max_attempts,
    )

    saver_context = (
        _fixed(checkpointer) if checkpointer is not None else checkpointer_for(settings.database_url)
    )
    try:
        async with saver_context as saver:
            graph = build_investigation_graph(deps).compile(checkpointer=saver)
            runner = InvestigationRunner(
                db, graph, pack, metrics, f"{INVESTIGATOR_PROMPT}+{REVIEWER_PROMPT}", clock
            )
            yield Container(
                settings=settings,
                pack=pack,
                db=db,
                metrics=metrics,
                queue=queue,
                ingestion=ingestion,
                graph=graph,
                runner=runner,
                worker=InvestigationWorker(db, queue, runner, settings.investigation, clock),
                decisions=DecisionService(db, graph, pack, clock),
                model_name=model_name,
            )
    finally:
        await db.dispose()


@asynccontextmanager
async def _fixed(saver: BaseCheckpointSaver[str]) -> AsyncIterator[BaseCheckpointSaver[str]]:
    yield saver


def checkpointer_for(database_url: str) -> AbstractAsyncContextManager[BaseCheckpointSaver[str]]:
    """A durable checkpointer beside the application's database, so a Case awaiting approval survives a restart."""
    if database_url.startswith("postgresql"):
        return _postgres_saver(database_url)
    if database_url.startswith("sqlite"):
        path = database_url.split(":///", 1)[1] if ":///" in database_url else ":memory:"
        if path in ("", ":memory:"):
            return _fixed(InMemorySaver())
        # A separate file keeps the checkpointer's writes from contending with the application's single writer.
        return _sqlite_saver(f"{path}.checkpoints")
    raise ValueError(f"No checkpointer for database URL scheme in {database_url.split(':', 1)[0]}")


@asynccontextmanager
async def _sqlite_saver(path: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        await saver.setup()
        yield saver


@asynccontextmanager
async def _postgres_saver(database_url: str) -> AsyncIterator[BaseCheckpointSaver[str]]:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg.rows import dict_row
    from psycopg_pool import AsyncConnectionPool

    conninfo = database_url.replace("postgresql+psycopg://", "postgresql://", 1)
    async with AsyncConnectionPool(
        conninfo,
        max_size=10,
        open=False,
        kwargs={"autocommit": True, "prepare_threshold": 0, "row_factory": dict_row},
    ) as pool:
        saver = AsyncPostgresSaver(pool)  # type: ignore[arg-type]
        await saver.setup()
        yield saver
