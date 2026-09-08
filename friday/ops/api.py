"""The board's data, over HTTP.

`web/` is the reader. It was written for two — a server-rendered board in
Python and a frontend in something else — and the first of those was deleted
(board `a-window-on-the-whole-path`, ticket 01) precisely because two readers
of one dataset drift: that one rendered the same prompts and provider errors
as this module while running none of them through `scrub`.

**This is the last place anything leaves the process**, so it is the last place
a credential can be caught. Task parameters and decision parameters are
model-extracted from text a stranger pasted into a chat, and an error recorded
against a failed send began life as a provider exception. All of it is scrubbed
on the way out, even where it was already scrubbed on the way in — the cost is
a regex over a few kilobytes, and the thing it prevents is unscoped access to
the operator's account.

**It is no longer only the way out.** One thing enters here: a channel's
context `overrides`, the layer of its YAML file the machine never writes
(board D7). That is context and not a decision — task state, approvals,
classifications and the agent's own memory are all still decided in Discord,
and none of them is reachable by any verb here. The consequence is
`check_exposure` at the foot of this file, which stopped warning and started
refusing: "unauthenticated is safe because it is read-only" was always an
argument about writes, and there is now a write.
"""

from __future__ import annotations

import logging
import socket
import pathlib
from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Path, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from friday.agent.instruction_prompt import channel_sections
from friday.domain.conversation import ConversationId
from friday.store.db import Database
from friday.domain.models import InboundEvent, Outbound, Task
from friday.outbox import FAILED
from friday.ops.redact import scrub
from friday.domain.states import TaskState

log = logging.getLogger(__name__)

__all__ = ["MAX_PAGE", "bind", "build_api", "check_exposure", "servable"]

#: The server decides how much it will hand over, not the caller.
MAX_PAGE = 200

#: How many messages one board render shows.
BOARD_MESSAGES = 25


def build_api(
    *,
    db: Database,
    provider_status: Callable[[], str],
    origins: list[str] | None = None,
    #: The live `ContextStore` the agents read from — the same object, not a
    #: copy, which is what one process buys (the architecture's first
    #: constraint). Given it, the routes that write a channel's `overrides`
    #: are registered; without it they are not registered at all rather than
    #: answering 503, the shape `Responder` already uses for its memory tools:
    #: what a caller can reach is composition, and a door that is not in the
    #: room should not be described.
    context_store: Any = None,
    #: `config.yaml`'s `triage.confidence_threshold`, served so the page can
    #: show a confidence against the line it is judged by. The flow screen
    #: hardcoded `0.70`, which agreed by luck and would have diverged
    #: silently the first time the operator tuned it — and a confidence
    #: without the threshold beside it is a number nobody can read.
    confidence_threshold: float | None = None,
) -> FastAPI:
    api = FastAPI(title="friday", docs_url="/api/docs", redoc_url=None)

    if origins:
        # Named exactly, and no credentials: the browser is not carrying an
        # identity here because there is none to carry.
        #
        # `PUT`/`POST` are here because one thing is now writable — a channel's
        # context `overrides` (board D7). That is the whole of the widening:
        # nothing that *decides* anything is reachable by either verb, and the
        # guard that made an unauthenticated board defensible moved to
        # `check_exposure` below, which stopped warning and started refusing.
        allow_methods = ["GET"] if context_store is None else ["GET", "PUT", "POST"]
        api.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=allow_methods,
            allow_headers=["*"],
        )

    @api.get("/api/board")
    async def board() -> dict:
        """Everything one page render needs, in one request.

        One aggregate rather than five calls: these are always rendered
        together and always read the same instant of the database.
        """
        messages = await db.page_messages(limit=BOARD_MESSAGES)
        calls = await db.calls_by_message(
            m.provider_message_id for m in messages
        )
        tasks = await db.tasks(limit=MAX_PAGE)
        return _clean(
            {
                "status": provider_status(),
                "confidence_threshold": confidence_threshold,
                "counts": await db.counts(),
                "failed": [_outbound(row) for row in await db.outbound(FAILED, limit=50)],
                "tasks_by_state": {
                    state.value: [_task(t) for t in tasks if t.state == state]
                    for state in TaskState
                },
                "messages": [
                    _message(m, calls.get(m.provider_message_id)) for m in messages
                ],
            }
        )

    @api.get("/api/messages")
    async def messages(
        limit: int = Query(50, ge=1, le=MAX_PAGE),
        before: str | None = None,
    ) -> list[dict]:
        """A page of the feed, newest first. `before` is a message id."""
        page = await db.page_messages(limit=limit, before=before)
        return _clean([_message(m, None) for m in page])

    @api.get("/api/messages/{provider}/{message_id}/model-calls")
    async def model_calls(
        provider: str = Path(...),
        message_id: str = Path(...),
    ) -> list[dict]:
        """The prompts behind one message.

        Never part of a list: they are large, and they carry whatever was in
        the conversation they were assembled from.
        """
        calls = await db.model_calls(message_id=message_id, limit=20)
        return _clean([asdict(c) | {"created_at": c.created_at} for c in calls])

    @api.get("/api/messages/{provider}/{message_id}/flow")
    async def message_flow(
        provider: str = Path(...),
        message_id: str = Path(...),
    ) -> dict:
        """Everything that followed from one message, in one request.

        The spine is a message and not a task (board D5): triage runs before a
        task exists, so its call correlates by `message_id` alone, and a
        task-spined view would lose both it and every `skip` — which is the
        outcome somebody looking at this screen most wants to interrogate.

        One aggregate rather than four calls joined in the browser (D6), for
        the reason `/api/board` gives above: four requests read four instants
        of a database being written to, and the path they render is one that
        never existed.

        A message nobody has triaged yet is a 200 with `decision: null` — that
        is a state, queued and unread, not an absence. Only a message that does
        not exist is a 404.
        """
        flow = await db.flow_for(provider=provider, message_id=message_id)
        if flow is None:
            raise HTTPException(
                status_code=404, detail=f"no message {message_id!r} from {provider!r}"
            )
        return _clean(
            {
                "message": _message(flow.message, None),
                "turn": [_message(m, None) for m in flow.turn],
                "decision": flow.decision,
                "triaged_at": flow.triaged_at,
                "task": _task(flow.task) if flow.task else None,
                "model_calls": [
                    asdict(c) | {"created_at": c.created_at} for c in flow.model_calls
                ],
                "tool_calls": [
                    asdict(t) | {"created_at": t.created_at} for t in flow.tool_calls
                ],
                "outbound": [_outbound(row) for row in flow.outbound],
            }
        )

    @api.get("/api/tasks/{task_id}/model-calls")
    async def task_model_calls(task_id: int = Path(...)) -> list[dict]:
        """Everything the model was asked while working on one task, in order.

        The question `message_id` cannot answer: an extractor runs on every
        pass of a task's graph against every message the reporter has sent,
        and a responder answers the task rather than any one message.
        """
        return _clean([
            asdict(c) | {"created_at": c.created_at}
            for c in await db.calls_for_task(task_id)
        ])

    @api.get("/api/tasks/{task_id}/calls")
    async def task_calls(task_id: int = Path(...)) -> dict:
        """What one task asked a model *and* what it reached for, plus what
        that cost.

        Both kinds together because they are one sequence — a tool call is
        usually the answer to the model call before it — and because the task
        screen claimed to interleave them while only ever fetching the model
        half: `tool_calls` had no per-task route at all, so ticket 07 of the
        previous board recorded what an agent reached for and this screen
        could not show it.

        Two lists rather than one merged one: they are different shapes, and
        merging them here would mean inventing a tag for a reader that can
        interleave on `created_at` itself.
        """
        calls = await db.calls_for_task(task_id)
        tools = (await db.tools_for_tasks([task_id])).get(task_id, [])
        return _clean(
            {
                "model_calls": [
                    asdict(c) | {"created_at": c.created_at} for c in calls
                ],
                "tool_calls": [
                    asdict(t) | {"created_at": t.created_at} for t in tools
                ],
                "spent": sum(c.input_tokens + c.output_tokens for c in calls),
            }
        )

    @api.get("/api/model-calls")
    async def recent_model_calls(
        uncorrelated: bool = False,
        limit: int = Query(20, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        """The newest calls, whatever they were about.

        `uncorrelated=true` narrows to the rows that name no message — the
        summariser's, and any agent working outside a task. They were stored
        and unreachable: the per-message route filters by message, and asking
        the store for `message_id=None` means "do not filter".
        """
        calls = await db.model_calls(uncorrelated=uncorrelated, limit=limit)
        return _clean([asdict(c) | {"created_at": c.created_at} for c in calls])

    @api.get("/api/channels/{channel_id}/memories")
    async def channel_memories(
        channel_id: str = Path(...),
        limit: int = Query(200, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        """This channel's memories, live or deleted, newest first — the
        operator's own view.

        Not scoped by agent the way `memory_search` is: a tool asks "what do
        I know", a person here asks "what does this room's memory say, and
        who wrote or removed each line" — `memory_delete` soft-deletes for
        exactly this route.

        Bounded like every other list route here, and for the same reason a
        channel's memory needed a cap at all: live rows stop at
        `Database.MEMORY_PER_CHANNEL`, but a deleted one is never purged, so
        an unbounded read here would grow with every correction a channel has
        ever had rather than with what it currently holds.
        """
        return _clean([
            asdict(m) | {
                "created_at": m.created_at,
                "updated_at": m.updated_at,
                "deleted_at": m.deleted_at,
            }
            for m in await db.memories_for_channel(channel_id, limit=limit)
        ])

    @api.get("/api/conversations")
    async def conversations() -> list[dict]:
        """The rooms, newest first — what the left-hand list renders."""
        return _clean(await db.rooms())

    @api.put("/api/conversations/{conversation_id:path}/name")
    async def name_conversation(
        conversation_id: str = Path(...), body: dict[str, Any] = Body(...)
    ) -> dict:
        """Name a room, or take its name back with an empty string.

        The operator's label, and only theirs: it reaches no prompt and no
        agent reads it, which is what makes it a plain write rather than one
        of the `channel_context` kind. A room nobody has spoken in has no row
        to name, and saying so beats creating one.

        `:path` on the segment because a conversation id carries a `/` when
        it names a thread, and the default converter stops at one.
        """
        name = body.get("name")
        if not isinstance(name, str):
            raise HTTPException(422, "body needs a 'name' string")
        if not await db.name_conversation(conversation_id, name):
            raise HTTPException(404, f"no conversation {conversation_id!r}")
        return _clean({"id": conversation_id, "name": name.strip() or None})

    @api.get("/api/tasks")
    async def tasks(
        state: TaskState | None = None,
        limit: int = Query(50, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        found = (
            await db.tasks_in_state(state, limit)
            if state is not None
            else await db.tasks(limit=limit)
        )
        return _clean([_task(t) for t in found])

    @api.get("/api/spend")
    async def spend() -> dict:
        """Tokens spent today, in total and per agent.

        `Database.spent_today` has existed since ticket 03 of
        `nothing-runs-unmeasured` and nothing could reach it: the ceiling it
        feeds (`daily_token_budget`) is unset by default, deliberately,
        because a number guessed before anyone knows what a normal day costs
        makes the first busy day look like a fault. This route is how an
        operator would ever learn what to set it to.

        Per agent as well as in total, because they are different jobs against
        different models — the classifier on every mention and the responder
        on a few are not one pool.
        """
        return _clean(
            {
                "total": await db.spent_today(),
                "by_agent": await db.spent_today_by_agent(),
            }
        )

    @api.get("/api/outbound")
    async def outbound(
        state: str | None = None,
        limit: int = Query(50, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        """What was sent, what is waiting, and what nobody could deliver."""
        return _clean([_outbound(r) for r in await db.outbound(state, limit=limit)])

    if context_store is not None:
        _mount_context(api, context_store)

    _mount_page(api)
    return api


#: Where `npm run build` puts the page, relative to the repo root. Served by
#: this app rather than by a second server — one process, one container.
PAGE = pathlib.Path(__file__).resolve().parents[2] / "web" / "dist"


def servable(page: pathlib.Path, path: str) -> pathlib.Path | None:
    """The file under `page` that `path` names, or `None` for anything else.

    `None` covers three cases the caller treats alike — the SPA's own routes,
    a file that does not exist, and **a path trying to leave `page`** — and
    the third is why this is a function rather than two lines inline.

    It was two lines inline, and it served the repository. `@api.get(
    "/{path:path}")` matches slashes by design, uvicorn percent-decodes the
    target *before* routing, and `pathlib`'s `/` walks upward without
    complaint, so `/../../.env` and `/%2e%2e/%2e%2e/.env` both returned the
    file. `PAGE` is `<repo>/web/dist`, which puts `.env`, `config.yaml` and
    the whole task database two `..` away, and `/proc/self/environ` — where a
    container's injected `DISCORD_USER_TOKEN` lives — a few more.

    That made this the one route that both bypasses `scrub` (`FileResponse`
    streams bytes; `_clean` never sees them) and can read the credential file
    directly. Which is the board's own scrub gap, the stated reason
    `friday/board/` was deleted, reintroduced by the commit that replaced it.

    `resolve()` before comparing, so a symlink planted inside the bundle is
    caught too — checking the unresolved path would not see it.
    """
    if not path:
        return None
    root = page.resolve()
    candidate = (page / path).resolve()
    if not candidate.is_relative_to(root):
        log.warning("refused a page request that left %s: %r", root, path)
        return None
    return candidate if candidate.is_file() else None


def _mount_page(api: FastAPI) -> None:
    """Serve `web/dist` under `/`, if it has been built.

    Mounted last, so every `/api/...` route above matches first. Absent when
    nobody has run `npm run build` — which is the state of a fresh checkout
    and of every test in this suite, and is why this is a condition rather
    than an assumption. The API is usable on its own; that is what
    `serve_board.py` is.
    """
    if not (PAGE / "index.html").exists():
        log.info("no built page at %s — serving the API alone", PAGE)
        return

    api.mount("/assets", StaticFiles(directory=PAGE / "assets"), name="assets")

    @api.get("/{path:path}", include_in_schema=False)
    async def page(path: str) -> FileResponse:
        """The SPA's own routes are not files. A request for `/flow/123`
        reaches the browser's router, not the filesystem, so anything that is
        not a real file is answered with `index.html` rather than a 404."""
        found = servable(PAGE, path)
        return FileResponse(found if found is not None else PAGE / "index.html")


def _mount_context(api: FastAPI, store: Any) -> None:
    """The one part of this API that writes (board D7).

    `docs/SPEC.md` said "any interaction on the web page" was out of scope,
    and this narrows that rather than deleting it. The rule exists so that
    **decisions** have one home, and a channel's `overrides` is not a
    decision — it is context, what is true about a room, and it is the one
    section the machine is forbidden to touch. Nothing that decides anything
    is reachable here: not a task's state, not an approval, not a
    classification, not the agent's own memory.
    """

    @api.get("/api/channels")
    async def channels() -> list[str]:
        """Channels that have a context file. Empty is the shipped state —
        `context/` has never had one written to it."""
        return _clean(store.known_channels())

    def _named(channel_id: str) -> str:
        """A channel id the store will accept, or a 400 saying why.

        `ContextStore.path_for` refuses ids that are not ids — a path, `.`,
        `..`, or `base`, which is the file that reaches *every* channel.
        Unhandled that arrives as a 500, which reads as "the server is
        broken" rather than "that is not a channel"; ticket 03 made the same
        objection about `FileExistsError`.
        """
        try:
            store.path_for(channel_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        return channel_id

    @api.get("/api/channels/{channel_id}/context")
    async def channel_context(channel_id: str = Path(...)) -> dict:
        """The three layers separately, and what they merge to.

        Separately because the page has to show what an edit is *overriding*:
        a key typed into `overrides` that also exists in `derived` silently
        shadows the summariser forever, which is a legitimate thing to want
        and a bad thing to do by accident.

        `live` is what the running agents are currently using — held from the
        last reload — and it differs from what is on disk exactly when
        somebody has saved and not reloaded. That difference is the one rule
        of D8 made visible.

        `prompt` is the three layers rendered by the seam that actually feeds
        an agent, `instruction_prompt.channel_sections`, rather than a merge
        of them. That distinction is not cosmetic and it cost a test to find:
        `ChannelContext.merged()` exists, and **nothing has ever rendered a
        prompt from it** — the layers reach a model as three separate labelled
        sections, and the model is told what each one means. So a key in both
        `derived` and `overrides` does not resolve to one value the way a
        merge implies; the model sees both and reconciles them itself. That is
        what `also_in` is warning about, and it is a worse thing to do by
        accident than a shadowed dict key would be.
        """
        on_disk = store.load(_named(channel_id))
        held = store.context(channel_id)
        return _clean(
            {
                "channel_id": channel_id,
                "exists": store.path_for(channel_id).exists(),
                "base": on_disk.base,
                "derived": on_disk.derived,
                "overrides": on_disk.overrides,
                # What an agent is actually told about this room, through the
                # one seam that renders it — including the escaping, so the
                # page shows what the model reads rather than what was typed.
                "prompt": channel_sections(on_disk),
                "live": channel_sections(held) if held is not None else None,
                "also_in": sorted(
                    set(on_disk.overrides) & (set(on_disk.derived) | set(on_disk.base))
                ),
            }
        )

    @api.post("/api/channels/{channel_id}/context", status_code=201)
    async def create_channel_context(channel_id: str = Path(...)) -> dict:
        """Create a file for a channel that has none — what `init_channel.py`
        does from a terminal.

        `FileExistsError` is the store's own guard and it means something
        specific: `overrides` is never clobbered, an operator's second `init`
        included. It reaches the page as a 409, not a 500.
        """
        try:
            store.init_channel(_named(channel_id))
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return _clean({"channel_id": channel_id, "created": True})

    @api.put("/api/channels/{channel_id}/context/overrides")
    async def set_overrides(
        channel_id: str = Path(...), body: dict[str, Any] = Body(...)
    ) -> dict:
        """Replace a channel's `overrides` from key/value pairs.

        Pairs, not YAML (D9): `merged()` is a flat dict with no schema, and a
        raw-YAML field would make "this channel's file is malformed and it now
        has no context" a state the UI can produce. So values must be strings,
        and anything else is a 422 rather than something that reaches a prompt
        as a rendered `dict`.

        Stored plain. `ChannelContext`'s own rule — *"Every value here is
        plain text. Escaping happens once, on the way into a prompt"* — so
        escaping here would show the model `&amp;lt;b&amp;gt;`.

        Takes effect on reload, not now. That is the one rule (D8), and
        `GET .../context` exposes the difference as `live`.
        """
        overrides = body.get("overrides")
        if not isinstance(overrides, dict):
            raise HTTPException(422, "body needs an 'overrides' object")
        for key, value in overrides.items():
            if not isinstance(value, str):
                raise HTTPException(
                    422,
                    f"{key!r} is a {type(value).__name__}; overrides are key/value "
                    "pairs of text, because every value here is rendered into a "
                    "prompt as a line",
                )
        if not store.path_for(_named(channel_id)).exists():
            raise HTTPException(
                404, f"{channel_id} has no context file — create it first"
            )
        store.set_overrides(channel_id, overrides)
        return _clean({"channel_id": channel_id, "saved": True, "live": False})

    @api.post("/api/context/reload")
    async def reload_context() -> dict:
        """Make every file on disk live — the page's edits and a hand-edit
        alike, which is what keeps it one rule rather than two.

        Reports what would not parse, because unlike startup there is somebody
        watching this one.
        """
        # Scrubbed like everything else, and here it is not ceremony: a
        # `yaml.YAMLError` quotes the source line it failed on, so an
        # unscrubbed `problems` hands back the content of the file that
        # could not be parsed — operator-written, and able to hold whatever
        # they pasted into it.
        problems = store.reload()
        return _clean({"reloaded": store.known_channels(), "problems": problems})


def _message(message: InboundEvent, call) -> dict:
    """The wire shape of one message.

    `is_task` and `is_enrichment` ride on the same payload as the message
    itself: the store populates both fields on `InboundEvent` (the task
    link from the messages table, the enrichment link from a join
    against `memories.source_message_id`), so the screen does not need a
    second round trip to render its markers.
    """
    return {
        "provider": message.provider,
        "provider_message_id": message.provider_message_id,
        "conversation": str(message.conversation),
        "author_name": message.author_name,
        "text": message.text,
        "created_at": message.created_at,
        "mention_type": message.mention_type,
        "is_own": message.is_own,
        # Markers for the Rooms screen. `is_task` says "this message
        # opened a task"; `is_enrichment` says "an agent wrote a memory
        # while processing this message". Both render as glyphs on the
        # message row.
        "is_task": message.task_id is not None,
        "is_enrichment": message.is_enrichment,
        # A summary only. The prompt is a separate request, on purpose.
        "model_call": (
            {
                "agent": call.agent,
                "model": call.model,
                "input_tokens": call.input_tokens,
                "output_tokens": call.output_tokens,
            }
            if call
            else None
        ),
    }


def _task(task: Task) -> dict:
    return {
        "id": task.id,
        "conversation": str(task.conversation),
        "type": task.type,
        "state": task.state,
        "confidence": task.confidence,
        "params": task.params,
        "created_at": task.created_at,
    }


def _outbound(row: Outbound) -> dict:
    return {
        "id": row.id,
        "task_id": row.task_id,
        "conversation": str(row.conversation),
        "kind": row.kind,
        "sender": row.sender,
        "text": row.text,
        "reply_to": row.reply_to,
        "state": row.state,
        "attempts": row.attempts,
        "last_error": row.last_error,
    }


def _clean(value: Any) -> Any:
    """Scrub every string on the way out, however deeply it is nested.

    Applied to the whole response rather than to the fields thought to be
    risky, because the field nobody thought about is the one that leaks.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    return value


_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
#: Present in a container. Checked rather than configured, because a
#: boolean that switches off a safety check is a boolean that gets
#: copied into a shell on a laptop.
_CONTAINER_MARKER = pathlib.Path("/.dockerenv")


def check_exposure(host: str, *, token: str | None) -> None:
    """Refuse to serve this to a network without a credential.

    The board has no authentication, and the design said that was safe because
    it is read-only. That argument was always about *writes*. Reading it hands
    over every captured message and every model prompt, and what actually made
    that safe was that it only ever answered on loopback.

    **There is a write now** (board D7): a channel's context `overrides`, which
    reaches the instructions of every agent working in that room. So this is no
    longer a judgement about disclosure — somebody who reaches this can change
    what the agent believes about a room, and every reply after that carries
    it — and the branch that used to let a container through is gone.

    That branch was not wrong when it was written. A container's loopback is
    unreachable from outside it, so binding there means the port mapping never
    arrives; `0.0.0.0` inside one means "this container", and who can reach
    *that* is the publish rule one layer out — `ports: ["127.0.0.1:8086:8086"]`
    in compose.yaml. But that reasoning trusts a compose file this process
    cannot see, which is a fine thing to do about reads and not about writes.
    """
    if host in _LOOPBACK or token:
        return
    inside = " inside a container" if _CONTAINER_MARKER.exists() else ""
    raise SystemExit(
        f"refusing to serve the board on {host}{inside}: it is unauthenticated, "
        "it shows every captured message and model prompt, and it now accepts "
        "writes — a channel's context overrides, which reach the instructions "
        "of every agent in that room.\n"
        "Set BOARD_TOKEN, or bind loopback and publish it with "
        'ports: ["127.0.0.1:8086:8086"].'
    )


def bind(host: str, port: int) -> socket.socket:
    """Take the port before the server starts.

    Uvicorn's own failure here is `sys.exit(3)` from inside a task, which
    unwinds through the TaskGroup as sixty lines of traceback ending in
    `SystemExit: 3`. The one fact that matters — something is already on the
    port — is somewhere in the middle of it. Binding first puts the failure
    where it can be said in a sentence, and hands the server a socket that is
    already ours, so nothing can take it in between.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind((host, port))
    except OSError as exc:
        sock.close()
        raise SystemExit(
            f"cannot serve the board on {host}:{port} — the port is already in "
            f"use ({exc.strerror}). Something else is running: another copy of "
            "the agent, or serve_board.py."
        ) from None
    sock.listen()
    return sock
