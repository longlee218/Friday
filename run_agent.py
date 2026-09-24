"""Composition root: the only place adapters are constructed."""

from __future__ import annotations

import asyncio
import logging
from contextlib import AsyncExitStack
from dataclasses import asdict
import os
from pathlib import Path

# Pydantic AI prints a Logfire banner to stdout at first use unless this is set;
# observability stays off unless configured (research doc, 2026-09-22).
os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from alembic import command
from alembic.config import Config
from dotenv import load_dotenv

from friday.kernel.memory.channel_context import ContextRebuilder
from friday.kernel.config import ConfigError, load_config
from friday.store.db import Database
from friday.kernel.inbox import Inbox
from friday.kernel.ops.api import bind, build_api, check_exposure
from friday.kernel.ops.liveness import Heartbeat, Liveness
from friday.kernel.harness.mcp import build as build_mcp, name_of
from friday.kernel.ops.redact import Redacting, install_excepthook
from friday.kernel.outbox import Outbox, record_decision
from friday.kernel.ops.single_instance import single_instance_lock
from friday.kernel.providers import CredentialRejected
from friday.kernel.providers.discord.user import DiscordUserProvider
from friday.kernel.providers.discord.bot import DiscordBot
from friday.kernel.responder import Responder
from friday.kernel.harness.skills import SkillLibrary
from friday.kernel.domain.models import ModelCall
from friday.kernel.triage.runner import TriageRunner
from friday.kernel.pool.pool import Pool

log = logging.getLogger("friday")


async def ingest(inbox: Inbox) -> None:
    """Capture mentions. Never waits on anything slow."""
    async for _ in inbox.stream():
        pass


async def serve_board(db, provider, config, sock, threshold) -> None:
    """The board's server runs in this process like everything else.

    Only the JSON API for now: the server-rendered page was deleted with the
    board it rendered, and `web/` has not replaced it yet (board ticket 05).
    Unauthenticated, which is what `check_exposure` above makes conditional on
    answering only on loopback — that argument was always about reads, and it
    is tightened once anything here writes (board ticket 04).
    """
    import uvicorn

    status = lambda: (  # noqa: E731
        "connected" if provider.reconnected.is_set() else "connecting"
    )
    check_exposure(config.board_host)

    app = build_api(
        db=db,
        provider_status=status,
        origins=list(config.board_origins),
        # Asked of the runner, not read out of config: which knobs triage
        # has is triage's business. The page draws a confidence against this
        # line and must not invent it.
        confidence_threshold=threshold,
        repo_root=config.repo_root or None,
    )
    # Nothing is mounted at `/` until `web/` exists (board ticket 05). The
    # server-rendered page that used to live there was deleted with the
    # database it rendered — see that board's D1: two renderers of one dataset
    # have drifted here twice, and the second time one of them was not
    # scrubbing credentials.

    server = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
    log.info(
        "api on http://%s:%d (start at /api/board)",
        config.board_host,
        config.board_port,
    )
    await server.serve(sockets=[sock])


async def run() -> None:
    async with AsyncExitStack() as stack:
        await _run(stack)


async def _run(stack: AsyncExitStack) -> None:
    config = load_config()
    # Part of loading it: a graph node whose clock would cut its model's run
    # short is a configuration error, refused before anything is opened.
    from friday.kernel.dag.router import check_graphs

    check_graphs(config)
    # Memory kinds register themselves into their registry (ticket 12), which the
    # store reads for a kind's writers, schema, natural key and reader routing.
    # Filled before anything opens the database or writes a memory.
    from friday.kernel.memory.registry import register_all_memory_kinds

    register_all_memory_kinds(config)
    Path(config.database_path).parent.mkdir(parents=True, exist_ok=True)

    token = os.environ.get("DISCORD_USER_TOKEN")
    if not token:
        raise SystemExit(
            "DISCORD_USER_TOKEN is not set. Put it in .env (see .env.example) "
            "or export it before running."
        )
    # One agent at a time (ticket 04; see `single_instance` for why). The OS
    # releases the lock when this process exits, so `stack` closing it is for a
    # clean shutdown, not crash recovery — there is no stale lock file to sweep.
    lock = single_instance_lock(config.database_path)
    stack.callback(lock.close)
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

    skills = SkillLibrary.build(config)

    provider = DiscordUserProvider(token=token)
    inbox = Inbox.build(config, provider=provider, db=db)

    async def marked(
        *, provider_message_id: str, mark, by: str, taking_back: bool
    ) -> None:
        """The operator reacted to a classification. Record it; change nothing
        else about the classification itself — but also resolve any candidate
        memory waiting on this same message (board
        `what-the-room-already-knows`, ticket 12, D19, D20).

        Marking a classification has no effect on the message it concerns —
        nothing is re-sent, nothing is undone. It only decides whether that
        classification is ever shown back to the classifier as an example.
        A pending candidate is different: this is the mark that decides
        whether it becomes a memory at all, which is D19's own point — "the
        gesture that already confirms a classification" is this one, not a
        second gesture to learn.
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
            #
            # A candidate already resolved by the reaction being *added* is
            # not undone by it being taken back — the same way `Verdict`
            # itself is not "un-recorded" retroactively into whatever a
            # classifier did with it meanwhile. Resolution only ever runs on
            # the add.
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
        await db.resolve_candidates_for_message(
            provider_message_id=provider_message_id, mark=str(mark), by=by,
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
    from plugins.devops.sources import DECLARED

    servers = []
    for server in build_mcp(config.mcp_servers, allowed=DECLARED):
        try:
            await stack.enter_async_context(server)
        except Exception as refused:  # noqa: BLE001 - one server, not the boot
            # **A tool server that will not connect does not stop the agent.**
            # The commonest reason is that nobody has signed in to it yet
            # (`authorize.py`), and a process that refuses to start until a
            # person opens a browser is a process nobody can restart
            # unattended. The graph says which source it wanted and skips.
            log.warning(
                "mcp %s is not available and is being skipped — %s: %s",
                name_of(server), type(refused).__name__, refused,
            )
            continue
        servers.append(server)
    if servers:
        log.info("mcp: %s", ", ".join(name_of(s) for s in servers))

    # Register the workflow's extraction agents. Composition root does not
    # know about each one — it asks the extractor module to wire itself
    # from config. Adding a new extractor is a change in
    # `friday/kernel/extraction/`, not here.
    from friday.kernel.extraction import register_extractors

    register_extractors(
        config,
        skills=skills,
        record=record_call,
        spent=db.spent_today,
    )

    # Register the workflow graphs. Same shape as the extractors above and for
    # the same reason: which task types have a graph is the graph module's
    # business, not this one's. Registration is explicit rather than a side
    # effect of importing, so a test can choose the path it exercises.
    from friday.kernel.dag.router import register_dags

    register_dags(
        config,
        servers={name_of(s): s for s in servers},
        skills=skills,
        # `api_issue`'s one model node is built here, the same way the
        # extractors and the responder are — so its calls are recorded and
        # counted against the same daily budget as everybody else's.
        record=record_call,
        spent=db.spent_today,
        # The store the DBOS adapter rebuilds each run's Deps from and writes
        # node_runs through — which also registers the graphs on the adapter.
        db=db,
    )

    from friday.kernel.dag import adapter

    # The outbox and its senders are built *before* DBOS launches, for the same
    # reason the graphs are registered before it (ticket 07): launch recovers
    # workflows a previous process left running, and a delivery interrupted
    # mid-send is one of them — recovery re-enters `deliver_once`, which must
    # already be registered with live senders or the resumed step has nothing to
    # call. So the button-decision callback, the bot, the senders and the outbox
    # come first, and `register_outbox` runs ahead of `launch`.
    async def decided(*, outbound_id: int, approved: bool, by: str, by_id: int) -> None:
        """What a button press means.

        The identity check lives in `record_decision`, not here: only the
        operator may release a reply that goes out in their name, so the
        decider's authenticated id is checked against `operator_id` before the
        row can become sendable — never trusted from whatever button was
        pressed.
        """
        await record_decision(
            db,
            outbound_id=outbound_id,
            approved=approved,
            by=by,
            by_id=by_id,
            operator_id=config.operator_id,
        )

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
        # Each delivery runs inside a DBOS workflow (ticket 07), so a crash
        # mid-send resumes exactly once — no double-post, no silent loss. The
        # adapter is the one module that names the vendor; the step calls back
        # into `deliver_once` for the delivery itself.
        durable=adapter.deliver_outbound,
    )
    adapter.register_outbox(outbox.deliver_once)

    # Durable workflows run on DBOS (ticket 06), on their own SQLite system
    # database beside the application one. Launch after the graphs and the
    # outbox delivery are registered, so recovery of any workflow left running
    # by a previous process can rebuild its Deps and resume its delivery; shut
    # it down on the way out. The adapter is the one module that names the vendor.
    adapter.launch("friday", str(Path(config.database_path).with_suffix(".system.db")))
    stack.callback(adapter.shutdown)

    responder = Responder.build(
        config,
        skills=skills,
        db=db,
        record=record_call,
        spent=db.spent_today,
    )
    pool = Pool.build(config, db=db, responder=responder)
    liveness = Liveness(
        db=db,
        gateway=provider,
        down_after_seconds=config.down_after_seconds,
        summary_at_hour=config.summary_at_hour,
    )
    heartbeat = Heartbeat(
        db=db,
        liveness=liveness,
        context_rebuilder=ContextRebuilder.build(
            config,
            db=db,
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
            group.create_task(
                serve_board(
                    db, provider, config, board_socket,
                    runner.confidence_threshold,
                )
            )
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
