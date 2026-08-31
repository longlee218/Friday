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
from friday.api import bind, build_api, check_exposure
from friday.board import build_board
from friday.liveness import Heartbeat
from friday.redact import Redacting
from friday.outbox import Outbox
from friday.providers import CredentialRejected
from friday.providers.discord import DiscordUserProvider
from friday.providers.discord.bot import DiscordBot
from friday.responder import Responder
from friday.tasks import TaskState
from friday.triage import Triage
from friday.triage.runner import TriageRunner
from friday.workflows.runner import WorkflowRunner

log = logging.getLogger("friday")


async def ingest(inbox: Inbox) -> None:
    """Capture mentions. Never waits on anything slow."""
    async for _ in inbox.stream():
        pass


async def serve_board(db, provider, config, sock) -> None:
    """The board runs in this process like everything else.

    Read-only, so it needs no authentication — there is nothing here to abuse,
    and every action happens in Discord.
    """
    import uvicorn

    status = lambda: (  # noqa: E731
        "connected" if provider.reconnected.is_set() else "connecting"
    )
    check_exposure(config.board_host, token=os.environ.get("BOARD_TOKEN"))

    app = build_api(
        db=db, provider_status=status, origins=list(config.board_origins)
    )
    # The JSON routes are declared first, so they match before this catches
    # everything else with the server-rendered page.
    app.mount("/", build_board(db=db, provider_status=status))

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    log.info(
        "board on http://%s:%d (api at /api/board)",
        config.board_host,
        config.board_port,
    )
    await server.serve(sockets=[sock])


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
        context_messages=config.ingest.context_messages,
    )

    responder_config = config.agents.get("responder")
    responder = (
        Responder(config=responder_config)
        if config.workflows.use_responder and responder_config
        else None
    )
    workflows = WorkflowRunner(
        db=db,
        auto_ask=config.workflows.auto_ask_for_details,
        responder=responder,
        tone_examples=int(
            (responder_config.options.get("tone_examples", 8))
            if responder_config
            else 8
        ),
        max_asks=config.workflows.max_asks,
    )
    if responder is not None:
        log.info(
            "responder on %s — its drafts need approval before they go out",
            responder_config.model,
        )
    async def decided(*, task_id: int, approved: bool, by: str) -> None:
        """What a button press means.

        Approving records who and when on the task, which is the only thing
        standing between a queued reply and the channel — the outbox selects on
        it, so nothing has to remember the reply was waiting.
        """
        if approved:
            await db.approve_task(task_id, by=by)
            log.info("task %d approved by %s", task_id, by)
            return
        await db.move_task(task_id, TaskState.NEEDS_HUMAN)
        log.info("task %d rejected by %s", task_id, by)

    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    bot = (
        DiscordBot(
            bot_token,
            operator_id=config.operator_id,
            board_url=f"http://{config.board_host}:{config.board_port}",
            on_decision=decided,
        )
        if bot_token and config.operator_id
        else None
    )
    if bot is None:
        log.warning(
            "no approval path: set DISCORD_BOT_TOKEN and operator_id, or drafts "
            "will queue and never be asked about"
        )

    senders = {"discord_user": provider}
    if bot is not None:
        senders["discord_bot"] = bot

    outbox = Outbox(
        db=db,
        # A conversation names a platform; a sender names an identity. Both
        # Discord clients speak into the same conversations, so these are two
        # different namespaces and the registry keys on the second.
        senders=senders,
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

    # Claimed before any task starts. Refusing from inside the TaskGroup would
    # unwind as a traceback; from here it is a sentence.
    board_socket = bind(config.board_host, config.board_port)

    # Four independent loops. Nothing that can block belongs in the one that
    # reads the gateway: a model call or a rate-limited send would stall the
    # consumer, which is the exact failure the recovery layer exists to prevent.
    try:
        async with asyncio.TaskGroup() as group:
            group.create_task(ingest(inbox))
            group.create_task(runner.run_forever())
            group.create_task(workflows.run_forever())
            group.create_task(outbox.run_forever())
            if bot is not None:
                group.create_task(bot.start())
            group.create_task(heartbeat.run_forever())
            group.create_task(serve_board(db, provider, config, board_socket))
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
