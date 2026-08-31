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
        log.info(
            "captured %s from %s in %s: %s",
            event.mention_type,
            event.author_name,
            event.channel_id,
            event.text,
        )
        # Temporary end-to-end check that outbound works. Ticket 06 replaces
        # this with the real path, where nothing posts without approval.
        if smoke and "hi there" in event.text.lower():
            await provider.reply(event, "What'sapp")


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
        "watching %d channel(s) for %s | triage on %s",
        len(config.ingest.watched_channels),
        ", ".join(sorted(config.ingest.mention_types)),
        triage_config.model,
    )

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
    try:
        migrate()
        asyncio.run(run())
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
