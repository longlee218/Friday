"""Composition root: the only place adapters are constructed."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import asdict
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from friday.memory.channel_context import ContextRebuilder, ContextStore
from friday.config import ConfigError, load_config
from friday.store.db import Database
from friday.inbox import Inbox
from friday.ops.api import bind, build_api, check_exposure
from friday.board import build_board
from friday.ops.liveness import Heartbeat, Liveness
from friday.agent.mcp import build as build_mcp
from friday.memory.notes import Promotion
from friday.ops.redact import Redacting, install_excepthook
from friday.outbox import Outbox
from friday.providers import CredentialRejected
from friday.providers.discord.user import DiscordUserProvider
from friday.providers.discord.bot import DiscordBot
from friday.responder import Responder
from friday.agent.skills import SkillLibrary
from friday.domain.models import ModelCall
from friday.domain.states import TaskState
from friday.triage.runner import TriageRunner
from friday.tasks.pool import Pool

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
    async with AsyncExitStack() as stack:
        await _run(stack)


async def _run(stack: AsyncExitStack) -> None:
    config = load_config()
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("DISCORD_USER_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_USER_TOKEN is not set. Put it in .env (see .env.example) "
            "or export it before running."
        )
    db = await Database.connect(config.database_path)

    async def record_call(entry) -> None:
        """Where everything this process asks of a model is written down.

        The one sink, built once, handed to every agent that is built below.
        It is here and not in `friday/store/` because which store an entry
        goes to is composition, and because the point of the seam is that no
        agent chooses whether to use it — see D1 in
        `.scratch/nothing-runs-unmeasured/SPEC.md`.

        Two kinds travel it: what the model was asked, and what the agent
        reached for. One seam because a second is a second thing to forget;
        they part here, at the only place that knows there are two tables.
        """
        if isinstance(entry, ModelCall):
            await db.record_model_call(**asdict(entry))
        else:
            await db.record_tool_call(**asdict(entry))

    context_store = ContextStore.build(config)
    skills = SkillLibrary.build(config)

    provider = DiscordUserProvider(token=token)
    inbox = Inbox(provider=provider, db=db, config=config.ingest)

    async def marked(
        *, provider_message_id: str, mark, by: str, taking_back: bool
    ) -> None:
        """The operator reacted to a classification. Record it; change nothing.

        Marking one has no effect on the message it concerns — nothing is
        re-sent, nothing is undone. It only decides whether that
        classification is ever shown back to the classifier as an example.
        """
        current = await db.verdict_for(
            provider=provider.name, provider_message_id=provider_message_id
        )

        if taking_back:
            # Only if the reaction they removed is the one currently on
            # record. Discord leaves an old reaction in place when a new one
            # is added, so ✅ then ❌ then remove-the-✅ is the natural order —
            # and clearing unconditionally would throw away the ❌ that is
            # still sitting on the message.
            if current is not None and current[0] == str(mark):
                await db.clear_verdict(
                    provider=provider.name,
                    provider_message_id=provider_message_id,
                )
            return

        await db.record_verdict(
            provider=provider.name,
            provider_message_id=provider_message_id,
            mark=str(mark),
            by=by,
        )

    provider.on_verdict = marked

    runner = await TriageRunner.build(
        config,
        db=db,
        still_typing=inbox.still_typing,
        record=record_call,
        spent=db.spent_today,
    )

    # Connected here rather than by whoever uses them: a connection has a
    # lifetime, and something has to close it. The stack unwinds with the run.
    # Before the agents, because one of them is handed this list.
    servers = build_mcp(config.mcp_servers)
    for server in servers:
        await stack.enter_async_context(server)
    if servers:
        log.info("mcp: %s", ", ".join(s.name for s in servers))

    # Register the workflow's extraction agents. Composition root does not
    # know about each one — it asks the extractor module to wire itself
    # from config. Adding a new extractor is a change in
    # `friday/extraction/`, not here.
    from friday.extraction import register_extractors

    register_extractors(config, record=record_call, spent=db.spent_today)

    # Register the workflow graphs. Same shape as the extractors above and for
    # the same reason: which task types have a graph is the graph module's
    # business, not this one's. Registration is explicit rather than a side
    # effect of importing, so a test can choose the path it exercises.
    from friday.dag.router import register_dags

    register_dags(
        config,
        servers={s.name: s for s in servers},
        skills=skills,
        context_store=context_store,
    )

    promotion = Promotion(db=db)
    # Read once, at build time: a promotion takes effect on the next start
    # rather than invalidating a warm prompt cache mid-run.
    learned = await promotion.render()

    responder = Responder.build(
        config,
        notes=learned,
        skills=skills,
        context_store=context_store,
        record=record_call,
        spent=db.spent_today,
    )
    pool = Pool.build(config, db=db, responder=responder)
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
    liveness = Liveness(
        db=db,
        gateway=provider,
        down_after_seconds=config.down_after_seconds,
        summary_at_hour=config.summary_at_hour,
    )
    heartbeat = Heartbeat(
        db=db,
        liveness=liveness,
        promotion=promotion,
        context_rebuilder=ContextRebuilder.build(
            config,
            store=context_store,
            db=db,
            promotion=promotion,
            record=record_call,
            spent=db.spent_today,
        ),
        interval_seconds=config.heartbeat_seconds,
        keep_model_calls_days=config.keep_model_calls_days,
        extra=inbox.tally,
    )

    if config.workflows.auto_ask_for_details:
        log.warning(
            "auto_ask_for_details is ON — the request for a correlationId will "
            "be sent under your name with no approval step"
        )
    log.info(
        "watching %d channel(s) for %s",
        len(config.ingest.watched_channels),
        ", ".join(sorted(config.ingest.mention_types)),
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
            group.create_task(pool.run_forever())
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
    # The filter cannot reach a crash: Python writes that straight to
    # stderr, and a traceback carries every argument in every frame.
    install_excepthook()
    try:
        migrate()
        asyncio.run(run())
    except ConfigError as exc:
        raise SystemExit(str(exc)) from exc


if __name__ == "__main__":
    main()
