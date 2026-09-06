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

from friday.agent.instruction_prompt import channel_sections
from friday.domain.conversation import ConversationId
from friday.store.db import Database
from friday.domain.models import InboundEvent, Outbound, Task
from friday.outbox import FAILED
from friday.ops.redact import scrub
from friday.domain.states import TaskState

log = logging.getLogger(__name__)

__all__ = ["MAX_PAGE", "bind", "build_api", "check_exposure"]

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

    @api.get("/api/outbound")
    async def outbound(
        state: str | None = None,
        limit: int = Query(50, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        """What was sent, what is waiting, and what nobody could deliver."""
        return _clean([_outbound(r) for r in await db.outbound(state, limit=limit)])

    if context_store is not None:
        _mount_context(api, context_store)

    return api


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
        return store.known_channels()

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
        on_disk = store.load(channel_id)
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
            store.init_channel(channel_id)
        except FileExistsError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from None
        return {"channel_id": channel_id, "created": True}

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
        if not store.path_for(channel_id).exists():
            raise HTTPException(
                404, f"{channel_id} has no context file — create it first"
            )
        store.set_overrides(channel_id, overrides)
        return {"channel_id": channel_id, "saved": True, "live": False}

    @api.post("/api/context/reload")
    async def reload_context() -> dict:
        """Make every file on disk live — the page's edits and a hand-edit
        alike, which is what keeps it one rule rather than two.

        Reports what would not parse, because unlike startup there is somebody
        watching this one.
        """
        problems = store.reload()
        return {"reloaded": store.known_channels(), "problems": problems}


def _message(message: InboundEvent, call) -> dict:
    return {
        "provider": message.provider,
        "provider_message_id": message.provider_message_id,
        "conversation": str(message.conversation),
        "author_name": message.author_name,
        "text": message.text,
        "created_at": message.created_at,
        "mention_type": message.mention_type,
        "is_own": message.is_own,
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

    The board has no authentication, and the design says that is safe because
    it is read-only. That argument was always about *writes*. Reading it hands
    over every captured message and every model prompt, and what actually made
    that safe was that it only ever answered on loopback. Binding wider is a
    different decision, and it has to be made deliberately.
    """
    if host in _LOOPBACK or token:
        return
    if _CONTAINER_MARKER.exists():
        # A container's loopback is unreachable from outside it, so binding
        # there would mean the port mapping never arrives. `0.0.0.0` here means
        # "this container", and who can reach *that* is the publish rule one
        # layer out — `ports: ["127.0.0.1:8086:8086"]` in compose.yaml.
        log.warning(
            "serving the board on %s inside a container — it is unauthenticated, "
            "so publish it to the host's loopback only",
            host,
        )
        return
    raise SystemExit(
        f"refusing to serve the board on {host}: it is unauthenticated and shows "
        "every captured message and model prompt. Bind it to loopback, or set "
        "BOARD_TOKEN."
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
