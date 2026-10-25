"""The `faa` command line: serve the API, migrate, dry-run and post Scenarios, generate and post Datasets."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

import typer
from pydantic import ValidationError

from fraud_audit_assist.config import Settings, get_settings
from fraud_audit_assist.domain.packs import load_pack
from fraud_audit_assist.domain.scenarios import Scenario, load_scenario, load_scenarios

app = typer.Typer(
    help="fraud-audit-assist: rules-first fraud detection with AI investigation.", no_args_is_help=True
)
scenario_app = typer.Typer(help="Hand-written journeys.", no_args_is_help=True)
dataset_app = typer.Typer(help="Seeded synthetic Datasets from Personas.", no_args_is_help=True)
db_app = typer.Typer(help="Database maintenance.", no_args_is_help=True)
app.add_typer(scenario_app, name="scenario")
app.add_typer(dataset_app, name="dataset")
app.add_typer(db_app, name="db")

PackOption = Annotated[Path | None, typer.Option(help="Pack directory; defaults to FAA_PACK_DIR.")]
RulesOption = Annotated[
    str | None, typer.Option(help="Rules version, e.g. v2; defaults to the pack's default.")
]
ApiUrl = Annotated[str, typer.Option(envvar="FAA_API_URL", help="Base URL of a running API.")]
ApiKey = Annotated[
    str | None, typer.Option(envvar="FAA_CLIENT_API_KEY", help="Ingest API key, sent as X-API-Key.")
]
AnalystKey = Annotated[
    str | None,
    typer.Option(envvar="FAA_CLIENT_ANALYST_KEY", help="Analyst API key, used to seed Confirmed Frauds."),
]


def _settings() -> Settings:
    """The validated settings, or a short list of what is wrong and exit code 2, instead of a traceback."""
    try:
        return get_settings()
    except ValidationError as error:
        typer.echo("Configuration error:", err=True)
        for problem in error.errors():
            location = ".".join(str(part) for part in problem["loc"]) or "settings"
            typer.echo(f"  {location}: {problem['msg'].removeprefix('Value error, ')}", err=True)
        raise typer.Exit(code=2) from error


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Runs the API with uvicorn, after checking the settings and the pack."""
    import uvicorn

    settings = _settings()
    try:
        load_pack(settings.pack_dir, settings.rules_version)
    except (ValueError, FileNotFoundError) as error:
        typer.echo(f"Pack error: {error}", err=True)
        raise typer.Exit(code=2) from error

    uvicorn.run("fraud_audit_assist.api.main:app", host=host, port=port, log_config=None, proxy_headers=True)


@db_app.command("upgrade")
def db_upgrade(revision: str = "head") -> None:
    """Applies Alembic migrations to FAA_DATABASE_URL."""
    from alembic import command
    from alembic.config import Config

    settings = _settings()
    command.upgrade(Config("alembic.ini"), revision)
    typer.echo(f"Database migrated to {revision}: {settings.database_url.split('@')[-1]}")


@db_app.command("rebuild-links")
def rebuild_links() -> None:
    """Rebuilds the Link Graph from the event store. Run with ingestion stopped."""
    from fraud_audit_assist.container import build_container

    async def run() -> int:
        async with build_container(_settings()) as container:
            return await container.ingestion.rebuild_links()

    typer.echo(f"Rebuilt links from {asyncio.run(run())} events.")


def _scenarios(paths: list[Path], pack_dir: Path) -> list[Scenario]:
    pack = load_pack(pack_dir).pack
    found: list[Scenario] = []
    for path in paths:
        found.extend(load_scenarios(path, pack) if path.is_dir() else [load_scenario(path, pack)])
    return found


@scenario_app.command("dry-run")
def scenario_dry_run(paths: list[Path], pack: PackOption = None, rules: RulesOption = None) -> None:
    """Scores Scenarios in memory, graph rules included, and prints the band after every step.

    Give the folder with the context Scenarios too (for example the whole scenarios/ folder) when a journey links
    to other Accounts.
    """
    from fraud_audit_assist.simulator.dry_run import dry_run, render

    pack_dir = pack or _settings().pack_dir
    loaded = load_pack(pack_dir, rules)
    results = asyncio.run(dry_run(loaded, _scenarios(paths, pack_dir)))
    typer.echo(f"Rules {loaded.pack.id} {loaded.rules.version}" + render(results))


@scenario_app.command("post")
def scenario_post(
    paths: list[Path],
    api_url: ApiUrl = "http://127.0.0.1:8000",
    api_key: ApiKey = None,
    analyst_key: AnalystKey = None,
    pack: PackOption = None,
) -> None:
    """Posts Scenarios to a running API in first-event order, then seeds their Confirmed Frauds."""
    from fraud_audit_assist.simulator.client import ApiClient

    scenarios = sorted(_scenarios(paths, pack or _settings().pack_dir), key=lambda s: s.first_event_at())

    async def run() -> None:
        async with ApiClient(api_url, api_key, analyst_key) as client:
            for scenario in scenarios:
                result = await client.post_events(scenario.events())
                typer.echo(
                    f"{scenario.name}: {len(result['ingested'])} ingested, {len(result['duplicates'])} already ingested"
                )
                if scenario.confirmed_fraud:
                    await client.confirm_fraud(scenario.account_id, scenario.confirmed_fraud)
                    typer.echo(f"{scenario.name}: Confirmed Fraud seeded")

    asyncio.run(run())


@dataset_app.command("generate")
def dataset_generate(
    seed: int = 42,
    accounts: int = 1000,
    pack: PackOption = None,
    out: Annotated[
        Path | None, typer.Option(help="Output file; defaults to datasets/<pack>-seed-<seed>-<n>.jsonl")
    ] = None,
) -> None:
    """Writes a seeded, labelled Dataset. The same pack, seed and size always give a byte-identical file."""
    from fraud_audit_assist.simulator.personas import generate_dataset, load_personas, write_dataset

    pack_dir = pack or _settings().pack_dir
    loaded = load_pack(pack_dir).pack
    target = out or Path("datasets") / f"{loaded.id}-seed-{seed}-{accounts}.jsonl"
    dataset = generate_dataset(load_personas(pack_dir, loaded), loaded, seed, accounts)
    write_dataset(dataset, target)
    fraud = sum(a.label.is_fraud for a in dataset)
    typer.echo(
        f"Wrote {target}: {len(dataset)} accounts, {fraud} fraud, {sum(len(a.events) for a in dataset)} events"
    )


@dataset_app.command("post")
def dataset_post(
    path: Path, api_url: ApiUrl = "http://127.0.0.1:8000", api_key: ApiKey = None, batch_size: int = 200
) -> None:
    """Posts a Dataset's events without their Labels, in event-time order across Accounts, so the graph builds as it
    would live. Switch Investigations off first (FAA_INVESTIGATION__ENABLED=false) unless you mean to spend."""
    from fraud_audit_assist.simulator.client import ApiClient
    from fraud_audit_assist.simulator.personas import read_dataset

    events = sorted(
        (e for a in read_dataset(path) for e in a.events), key=lambda e: (e.occurred_at, str(e.event_id))
    )

    async def run() -> None:
        async with ApiClient(api_url, api_key) as client:
            for start in range(0, len(events), batch_size):
                await client.post_events(events[start : start + batch_size])
        typer.echo(f"Posted {len(events)} events.")

    asyncio.run(run())


if __name__ == "__main__":  # pragma: no cover
    app()
