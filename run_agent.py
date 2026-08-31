"""Composition root: the only place adapters are constructed."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from friday.config import ConfigError, load_config
from friday.db import Database
from friday.inbox import Inbox
from friday.board import build_board
from friday.liveness import Heartbeat
from friday.redact import Redacting
from friday.outbox import Outbox
from friday.providers import CredentialRejected
from friday.providers.discord import DiscordUserProvider
from friday.triage import Triage
from friday.triage.runner import TriageRunner
from friday.workflows.runner import WorkflowRunner

log = logging.getLogger("friday")


async def ingest(inbox: Inbox, provider, smoke: bool) -> None:
    """Capture mentions. Never waits on anything slow."""
    async for event in inbox.stream():
        # Temporary end-to-end check that outbound works. Ticket 06 replaces
        # this with the real path, where nothing posts without approval.
        if smoke and "hi there" in event.text.lower():
            await provider.reply(event, "What'sapp")


async def serve_board(db, provider, port: int) -> None:
    """The board runs in this process like everything else.

    Read-only, so it needs no authentication — there is nothing here to abuse,
    and every action happens in Discord.
    """
    import uvicorn

    board = build_board(
        db=db,
        provider_status=lambda: (
            "connected" if provider.reconnected.is_set() else "connecting"
        ),
    )
    server = uvicorn.Server(
        uvicorn.Config(board, host="0.0.0.0", port=port, log_level="warning")
    )
    log.info("board on http://localhost:%d", port)
    await server.serve()


async def run() -> None:
    config = load_config()
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("DISCORD_USER_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_USER_TOKEN is not set. Put it in .env (see .env.example) "
            "or export it before running."
        )
    try:
        triage_config = config.agents["triage"]
    except KeyError:
        raise SystemExit("No 'triage' agent in config.yaml — see the agents section.")

    db = await Database.connect(config.database_path)
    provider = DiscordUserProvider(token=token)
    inbox = Inbox(provider=provider, db=db, config=config.ingest)
    runner = TriageRunner(
        db=db,
        triage=Triage(config=triage_config),
        confidence_threshold=float(
            triage_config.options.get("confidence_threshold", 0.7)
        ),
    )

    workflows = WorkflowRunner(
        db=db,
        auto_ask=config.workflows.auto_ask_for_details,
        max_asks=config.workflows.max_asks,
    )
    outbox = Outbox(
        db=db,
        # A conversation names a platform; a sender names an identity. Both
        # Discord clients speak into the same conversations, so these are two
        # different namespaces and the registry keys on the second.
        senders={"discord_user": provider},
        max_attempts=config.outbox.max_attempts,
        backoff_seconds=config.outbox.backoff_seconds,
    )
    heartbeat = Heartbeat(
        db=db,
        interval_seconds=config.heartbeat_seconds,
        keep_model_calls_days=config.keep_model_calls_days,
        extra=inbox.tally,
    )

    if config.workflows.auto_ask_for_details:
        log.warning(
            "auto_ask_for_details is ON — the request for a correlationId will "
            "be sent under your name with no approval step"
        )
    if config.ingest.capture_own_messages:
        log.warning(
            "capture_own_messages is ON — testing only; turn it off before the "
            "agent can reply, or it will answer itself"
        )
    log.info(
        "watching %d channel(s) for %s | triage on %s via %s",
        len(config.ingest.watched_channels),
        ", ".join(sorted(config.ingest.mention_types)),
        triage_config.model,
        triage_config.base_url,
    )
    for channel_id in sorted(config.ingest.watched_channels):
        log.info(
            "  channel %s — read up to %s",
            channel_id,
            await db.cursor_for(provider.name, channel_id) or "(nothing yet)",
        )
    # What is already in flight, so a restart does not look like a fresh start.
    log.info("picking up: %s", await heartbeat.summary())

    # Four independent loops. Nothing that can block belongs in the one that
    # reads the gateway: a model call or a rate-limited send would stall the
    # consumer, which is the exact failure the recovery layer exists to prevent.
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(
                ingest(inbox, provider, os.environ.get("SMOKE_ECHO") == "1")
            )
            group.create_task(runner.run_forever())
            group.create_task(workflows.run_forever())
            group.create_task(outbox.run_forever())
            group.create_task(heartbeat.run_forever())
            group.create_task(serve_board(db, provider, config.board_port))
    except* CredentialRejected as group_exc:
        raise SystemExit(
            f"Discord rejected the credential: {group_exc.exceptions[0]}"
        ) from group_exc
    finally:
        await db.close()


def migrate() -> None:
    """Bring the database up to date before anything opens it.

    Runs here rather than inside `run()` because Alembic's async environment
    calls `asyncio.run` itself, which cannot happen inside a running loop.
    Nothing else is started yet, so blocking is free.
    """
    command.upgrade(Config(str(Path(__file__).parent / "alembic.ini")), "head")


def main() -> None:
    load_dotenv()
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # On the handler, not on a logger: it has to cover the libraries too, which
    # is where a token would actually surface — an unhandled exception carrying
    # a request header, not our own code printing it on purpose.
    for handler in logging.getLogger().handlers:
        handler.addFilter(Redacting())
    try:
        migrate()
        asyncio.run(run())
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
