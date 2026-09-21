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

**It is no longer only the way out.** What enters here is the operator's own
memory rows — `origin=admin`, the knowledge only they have — through
`POST/PUT/DELETE /api/channels/{id}/memories` (board
`read-it-the-way-the-operator-does`, ticket 09). It replaced the first thing
that ever entered, a channel file's `overrides` (board D7), when the YAML
files went (ticket 10). It is context and not a decision — task state,
approvals and classifications are all still decided in Discord, and none of
them is reachable by any verb here. The consequence is `check_exposure` at
the foot of this file, which stopped warning and started refusing:
"unauthenticated is safe because it is read-only" was always an argument
about writes, and there is a write. It goes through the store's one write
door, so the schema check and the instruction-shape guard bind the
operator's hand as they bind a model's.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import pathlib
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import MISSING, asdict, is_dataclass
from dataclasses import fields as dataclass_fields
from typing import Any, Literal, get_args, get_origin, get_type_hints

from fastapi import Body, FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from friday.domain.conversation import ConversationId
from friday.domain.memory_guard import InstructionShaped
from friday.store.db import Database
from friday.domain.models import (
    MEMORY_DATA,
    FridayState,
    InboundEvent,
    Memory,
    MemoryKeyTaken,
    MemoryKind,
    natural_key,
    MemoryOrigin,
    MemoryRefused,
    Outbound,
    Task,
    writers_for,
)
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
        # `PUT`/`POST` came with the first thing that was writable — a
        # channel file's `overrides` (board D7), gone with the files (board
        # `read-it-the-way-the-operator-does`, ticket 10). Nothing that
        # *decides* anything is reachable by either verb, and the guard that
        # made an unauthenticated board defensible moved to `check_exposure`
        # below, which stopped warning and started refusing.
        #
        # `DELETE` came with the operator's own memory rows (ticket 09), which
        # are what the page writes now: a room's facts, runbooks and people.
        allow_methods = ["GET", "PUT", "POST", "DELETE"]
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
        openings = await db.opening_messages(t.id for t in tasks)
        return _clean(
            {
                "status": provider_status(),
                "confidence_threshold": confidence_threshold,
                "counts": await db.counts(),
                # D18: the model already gets the refusal; this is the
                # operator's own view of the same condition.
                "full_memory_channels": await db.full_memory_channels(),
                "failed": [_outbound(row) for row in await db.outbound(FAILED, limit=50)],
                "tasks_by_state": {
                    state.value: [
                        _task(t, openings.get(t.id)) for t in tasks if t.state == state
                    ]
                    for state in TaskState
                },
                "messages": [
                    _message(m, calls.get(m.provider_message_id)) for m in messages
                ],
            }
        )

    @api.get("/api/monitor")
    async def monitor() -> dict:
        """The Monitor screen's initial snapshot. SSE (ticket 05) takes
        over from here — the page asks once on mount, then subscribes
        to the event stream.

        One round trip rather than four: events, running tasks, the
        three counters and the day's spend are all answers the page
        asks in the same breath. A page that rendered five spinners
        for five endpoints is a page that took five round trips to
        look like one."""
        snap = await db.monitor_snapshot()
        return _clean(
            {
                "status": snap.status,
                "events": [_monitor_event(e) for e in snap.events],
                "running_tasks": [_running_task(t) for t in snap.running_tasks],
                "counts": {
                    "messages": snap.messages,
                    "untriaged": snap.untriaged,
                    "last_message_at": snap.last_message_at,
                    "spend_today": snap.spend_today,
                },
            }
        )

    @api.get("/api/events")
    async def events(request: Request) -> StreamingResponse:
        """Server-sent events. The Monitor screen subscribes here
        after the initial snapshot; this stream is what keeps the
        feed and the running-tasks panel live.

        `Last-Event-ID` is the standard SSE reconnect header. The
        bus replays every event since the last id the client saw;
        a reconnect after the browser's default 3s outage picks up
        where it left off without a polling round trip."""
        from friday.ops.events import get_bus

        bus = get_bus()
        # `last_event_id` arrives as a string; absent means "no
        # events missed". `int(...)` on "" raises — guard it.
        last_seen_raw = request.headers.get("Last-Event-ID", "")
        try:
            last_seen = int(last_seen_raw) if last_seen_raw else 0
        except ValueError:
            last_seen = 0

        queue, replay = await bus.subscribe()

        async def stream():
            try:
                # Replay first — events after `last_seen`, in order.
                for event in replay:
                    if event.id > last_seen:
                        yield _clean(_format_sse(event))
                # Then live events until the client disconnects.
                while True:
                    if await request.is_disconnected():
                        return
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    except asyncio.TimeoutError:
                        # A keep-alive comment every 15 seconds. SSE
                        # proxies and load balancers close idle
                        # connections; a comment costs one byte
                        # and tells them the stream is still alive.
                        yield ": keepalive\n\n"
                        continue
                    yield _clean(_format_sse(event))
            finally:
                await bus.unsubscribe(queue)

        return _clean(StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={
                # Disable proxy buffering so events reach the
                # browser the moment the bus publishes them. A
                # proxy that buffers holds them until the next
                # flush and the screen lags for the flush
                # interval.
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        ))

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
        opening = (
            (await db.opening_messages([flow.task.id])).get(flow.task.id)
            if flow.task
            else None
        )
        return _clean(
            {
                "message": _message(flow.message, None),
                "turn": [_message(m, None) for m in flow.turn],
                "decision": flow.decision,
                "triaged_at": flow.triaged_at,
                "task": _task(flow.task, opening) if flow.task else None,
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

    @api.get("/api/tasks/{task_id}/compaction")
    async def task_compaction(task_id: int = Path(...)) -> dict:
        """Whether node 0's own budget-based truncation has stopped trying
        for this task (board `what-the-room-already-knows`, ticket 08, D6).

        A log warning fires each time a compaction turns out ineffective,
        which is the one party this route is not for. This is the operator's
        own view of the same fact — a task with `ineffective_count` above
        zero is one whose own transcript truncation cannot help, and one
        that has reached `on_cooldown` is one node 0 has stopped re-checking
        on every pass.
        """
        count = await db.compaction_ineffective_count(task_id)
        return _clean(
            {
                "ineffective_count": count,
                "on_cooldown": count >= db.COMPACTION_COOLDOWN_AFTER,
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
            _memory_row(m)
            for m in await db.memories_for_channel(channel_id, limit=limit)
        ])

    @api.get("/api/channels/{channel_id}/memory-kinds")
    async def memory_kinds(channel_id: str = Path(...)) -> list[dict]:
        """The kinds an operator may write, and the fields each one's form
        needs — read off the same schemas `Database.memory_add` checks
        against, so the page cannot offer a field the store does not know.

        **Scoped to a channel because a field can name another row** (ticket
        19). `ServiceData.project` is a foreign key onto a `project` row's
        key, and until this route knew which room it was answering for it
        could only offer that as free text. A field declaring `names` comes
        back a `choice` over the keys that room actually holds, so the
        question stops having wrong answers that look like right ones.

        `names` rides along on the wire, and earns it: a room with no rows of
        that kind yet gets an empty `choices`, and only `names` tells the
        page whether that means "nothing to choose" or "this was never a
        choice". The first six rows ever typed were entered into exactly that
        empty state.
        """
        forms = [_kind_form(k) for k in MemoryKind if ADMIN in writers_for(k)]
        wanted = {
            field["names"]
            for form in forms for field in form["fields"] if field["names"]
        }
        keys = {kind: await _row_keys(db, channel_id, kind) for kind in wanted}
        for form in forms:
            for field in form["fields"]:
                if field["names"]:
                    field["choices"] = keys[field["names"]]
        return _clean(forms)

    @api.post("/api/channels/{channel_id}/memories", status_code=201)
    async def add_memory(
        channel_id: str = Path(...), body: dict[str, Any] = Body(...)
    ) -> dict:
        """Write a row as the operator (`origin=admin`) — the knowledge only
        they have: a runbook, a service's placement, who someone is.

        Through `Database.memory_add`, the one door, so the operator's hand
        meets the same schema check and instruction-shape guard a model's
        does. Each refusal comes back with the store's own reason: 422 for a
        payload that does not fit or a line that reads as an instruction,
        409 for a key an active row already holds or a full room.
        """
        kind = body.get("kind")
        if kind not in {k.value for k in MemoryKind}:
            raise HTTPException(422, f"kind must be one of {', '.join(MemoryKind)}")
        text = body.get("text") or ""
        if not isinstance(text, str):
            raise HTTPException(422, "text must be a string")
        with _refusals():
            written = await db.memory_add(
                _operator(channel_id), text, kind=kind, origin=ADMIN,
                key=body.get("key"), data=body.get("data"),
            )
        if written is None:
            raise HTTPException(
                409, f"{channel_id}'s memory is full — remove something first"
            )
        return _clean(_memory_row(written))

    @api.put("/api/channels/{channel_id}/memories/{memory_id}")
    async def update_memory(
        channel_id: str = Path(...),
        memory_id: str = Path(...),
        body: dict[str, Any] = Body(...),
    ) -> dict:
        """Correct a row in place, as the operator. `data`, when sent,
        replaces a structured row's payload and is checked again."""
        text = body.get("text") or ""
        if not isinstance(text, str):
            raise HTTPException(422, "text must be a string")
        with _refusals():
            updated = await db.memory_update(
                _operator(channel_id), memory_id, text,
                data=body.get("data"), origin=ADMIN,
            )
        if updated is None:
            raise HTTPException(404, f"no memory {memory_id!r} in {channel_id}")
        return _clean(_memory_row(updated))

    @api.delete("/api/channels/{channel_id}/memories/{memory_id}")
    async def delete_memory(
        channel_id: str = Path(...), memory_id: str = Path(...)
    ) -> dict:
        """Remove a row, as the operator — soft, like every deletion here,
        so the row stays listed with who removed it."""
        if not await db.memory_delete(_operator(channel_id), memory_id, origin=ADMIN):
            raise HTTPException(404, f"no memory {memory_id!r} in {channel_id}")
        return _clean({"id": memory_id, "deleted": True})

    @api.get("/api/channels/{channel_id}/candidates")
    async def channel_candidates(
        channel_id: str = Path(...),
        limit: int = Query(200, ge=1, le=MAX_PAGE),
    ) -> list[dict]:
        """This channel's candidate memories, newest first — pending and
        resolved alike (board `what-the-room-already-knows`, ticket 12,
        D19, D20).

        The "place for a person to look" the old staging tier never had: a
        `PENDING` row is read by no prompt and no tool, so this route is the
        only way to see one before it is marked, and a `REJECTED` row stays
        listed rather than deleted, so the operator can see what was
        proposed and turned down.
        """
        return _clean([
            asdict(c) | {
                "proposed_at": c.proposed_at,
                "resolved_at": c.resolved_at,
            }
            for c in await db.candidates_for_channel(channel_id, limit=limit)
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
        agent reads it, which is what makes it a plain write rather than a
        memory row. A room nobody has spoken in has no row
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
        openings = await db.opening_messages(t.id for t in found)
        return _clean([_task(t, openings.get(t.id)) for t in found])

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


#: Who the board's own memory routes write as.
ADMIN = MemoryOrigin.ADMIN


def _operator(channel_id: str) -> FridayState:
    return FridayState(channel_id=channel_id, agent="operator")


@contextmanager
def _refusals():
    """The store's refusals as the 422 they are, carrying its reason —
    except a key already held, which is a 409 like any other conflict."""
    try:
        yield
    except InstructionShaped as refused:
        raise HTTPException(422, str(refused)) from None
    except MemoryKeyTaken as taken:
        raise HTTPException(409, str(taken)) from None
    except MemoryRefused as refused:
        raise HTTPException(422, str(refused)) from None


def _memory_row(m: Memory) -> dict:
    return asdict(m) | {
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "deleted_at": m.deleted_at,
    }


async def _row_keys(db: Database, channel_id: str, kind: str) -> list[str]:
    """Every key a room's rows of one kind currently hold, for a field that
    names one of them.

    Read through the same `structured_memories` the graph reads by, and keyed
    through the same `natural_key` the store writes by — so what the form
    offers and what a lookup will find cannot be two different lists.
    """
    rows = await db.structured_memories(channel_id, kind=kind)
    found = {natural_key(kind, asdict(row), None) for row in rows}
    return sorted(key for key in found if key)


def _kind_form(kind: MemoryKind) -> dict:
    """One kind's form: prose, a key the operator names, and its fields."""
    shape = MEMORY_DATA[kind]
    return {
        "kind": kind.value,
        "prose": shape is None,
        "names_key": kind is MemoryKind.RUNBOOK,
        "fields": _form_fields(shape) if shape is not None else [],
    }


def _form_fields(shape: type, prefix: str = "") -> list[dict]:
    """A dataclass flattened to form fields: a nested dataclass becomes
    dotted names, a list of strings a comma-separated line, a `Literal` a
    choice, and anything else (a list of objects) JSON typed by hand."""
    hints = get_type_hints(shape)
    found = []
    for f in dataclass_fields(shape):
        name, hint = f"{prefix}{f.name}", hints[f.name]
        args = [a for a in get_args(hint) if a is not type(None)]
        optional = type(None) in get_args(hint) or f.default is not MISSING or (
            f.default_factory is not MISSING
        )
        if is_dataclass(hint):
            found += _form_fields(hint, f"{name}.")
            continue
        names = str(f.metadata.get("names", ""))
        if names:
            # A foreign key is a choice whatever its annotation says: it is
            # `str` like any other name, and the thing that makes it not free
            # text is the declaration, not the type.
            entry = {"type": "choice"}
        elif get_origin(hint) is Literal:
            entry = {"type": "choice", "choices": list(get_args(hint))}
        elif get_origin(hint) is list and args == [str]:
            entry = {"type": "list"}
        elif set(args or [hint]) <= {int, float}:
            entry = {"type": "number"}
        elif set(args or [hint]) <= {str}:
            entry = {"type": "text"}
        else:
            entry = {"type": "json"}
        found.append({
            "name": name,
            "required": not optional,
            "choices": [],
            # Empty for every field that is not a foreign key, which is most
            # of them. The kind's own name, so the route can fill the
            # choices and the page can say what is missing.
            "names": names,
            **entry,
        })
    return found


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
        # Which task, not only whether: a board card finds its own message
        # by this. Matching on the room instead gave two tasks in one room
        # the same message (the operator's report, 2026-09-18).
        "task_id": message.task_id,
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


def _task(task: Task, opening: dict | None = None) -> dict:
    return {
        "id": task.id,
        "conversation": str(task.conversation),
        "type": task.type,
        "state": task.state,
        "confidence": task.confidence,
        "params": task.params,
        "created_at": task.created_at,
        # The Monitor screen reads these two. `_task()` is the
        # converter for any task that has not been enriched with
        # activity data; `running_tasks()` writes the same shape
        # with both fields filled, and the contract test asserts
        # the keys agree. `None` and `0` are the placeholders for
        # the non-Monitor path (Board, Flow, etc.) which never
        # reads them.
        "last_activity_at": task.last_activity_at,
        "attempts": task.attempts,
        # The message that opened this task (`Database.opening_messages`), so
        # a board card shows and opens its own message rather than one it
        # found among the newest loaded — `None` when no message is linked.
        "opening": opening,
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
    `bytes` is the SSE wire form: the payload string is JSON that the
    browser parses, so it carries the same risk and goes through the
    same scrub."""
    if isinstance(value, (str, bytes)):
        scrubbed = scrub(value.decode("utf-8") if isinstance(value, bytes) else value)
        return scrubbed.encode("utf-8") if isinstance(value, bytes) else scrubbed
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


def _monitor_event(e) -> dict:
    return {
        "id": e.id,
        "type": e.type,
        "occurred_at": e.occurred_at,
        "agent": e.agent,
        "tool": e.tool,
        "latency_ms": e.latency_ms,
        "state": e.state,
    }


def _running_task(t) -> dict:
    return {
        "id": t.id,
        "type": t.type,
        "state": t.state,
        "room": t.room,
        "message_id": t.message_id,
        "last_activity_at": t.last_activity_at,
        "last_tool": t.last_tool,
        "attempts": t.attempts,
    }


def _format_sse(event) -> bytes:
    """One SSE frame: id, event, data, blank line.

    The wire format is fixed by the SSE spec (text/event-stream).
    `data` is JSON — the browser's EventSource parses it once.
    `event` is the type; the client hook dispatches on it. A blank
    line terminates the frame — that is what flushes the browser's
    parser.

    `_clean` runs every string through `scrub` on the way out. The
    store publishes an event whose payload is the row's own
    fields (agent, tool, task id) — none of them carries a token
    — but the rule applies to every string regardless, so the
    guard against future event types that do carry one is in
    place."""
    import json as _json
    scrubbed = _clean(event.payload)
    payload = {
        "id": event.id,
        "type": event.type,
        "occurred_at": event.occurred_at.isoformat(),
        "payload": scrubbed,
    }
    return (
        f"id: {event.id}\n"
        f"event: {event.type}\n"
        f"data: {_json.dumps(payload)}\n\n"
    ).encode("utf-8")
