"""The board's data, over HTTP.

The server-rendered board is one reader of this; a frontend written in
something other than Python is the other. Both see the same shapes.

**This is the last place anything leaves the process**, so it is the last place
a credential can be caught. Task parameters and decision parameters are
model-extracted from text a stranger pasted into a chat, and an error recorded
against a failed send began life as a provider exception. All of it is scrubbed
on the way out, even where it was already scrubbed on the way in — the cost is
a regex over a few kilobytes, and the thing it prevents is unscoped access to
the operator's account.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict
from typing import Any

from fastapi import FastAPI, Path, Query
from fastapi.middleware.cors import CORSMiddleware

from friday.conversation import ConversationId
from friday.db import Database
from friday.models import InboundEvent, Outbound, Task
from friday.outbox import FAILED
from friday.redact import scrub
from friday.tasks import TaskState

__all__ = ["MAX_PAGE", "build_api", "check_exposure"]

#: The server decides how much it will hand over, not the caller.
MAX_PAGE = 200

#: How many messages one board render shows.
BOARD_MESSAGES = 25


def build_api(
    *,
    db: Database,
    provider_status: Callable[[], str],
    origins: list[str] | None = None,
) -> FastAPI:
    api = FastAPI(title="friday", docs_url="/api/docs", redoc_url=None)

    if origins:
        # Named exactly, and no credentials: the browser is not carrying an
        # identity here because there is none to carry.
        api.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=False,
            allow_methods=["GET"],
            allow_headers=["*"],
        )

    @api.get("/api/board")
    async def board() -> dict:
        """Everything one page render needs, in one request.

        One aggregate rather than five calls: these are always rendered
        together and always read the same instant of the database.
        """
        messages = await db.page_messages(limit=BOARD_MESSAGES)
        calls = {c.message_id: c for c in await db.model_calls(limit=MAX_PAGE)}
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

    return api


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
    raise SystemExit(
        f"refusing to serve the board on {host}: it is unauthenticated and shows "
        "every captured message and model prompt. Bind it to loopback, or set "
        "BOARD_TOKEN."
    )
