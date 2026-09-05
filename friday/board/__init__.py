"""A read-only view of what the agent is doing, on :8086.

A **debug view**, not a control panel. It answers "is this working, and why did
it decide that?" — the question that otherwise requires reading a terminal that
has already scrolled past, or querying SQLite by hand.

It displays and nothing more. Every action happens in Discord, which is what
lets this run without authentication: there is nothing here to abuse.
"""

from __future__ import annotations

import html
from collections.abc import Callable

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from friday.store.db import Database
from friday.outbox import FAILED, QUEUED
from friday.domain.states import TaskState

__all__ = ["build_board"]

#: HTMX polls this back in place. Chosen over a websocket because the page is a
#: handful of rows and a refresh costs one query — a live channel would be more
#: moving parts to keep working than the thing it reports on.
_REFRESH_SECONDS = 5


def build_board(*, db: Database, provider_status: Callable[[], str]) -> FastAPI:
    board = FastAPI(docs_url=None, redoc_url=None)

    @board.get("/", response_class=HTMLResponse)
    async def page() -> str:
        return _PAGE.format(body=await _body(db, provider_status()))

    @board.get("/body", response_class=HTMLResponse)
    async def body() -> str:
        return await _body(db, provider_status())

    return board


async def _body(db: Database, status: str) -> str:
    counts = await db.counts()
    messages = await db.page_messages(limit=25)
    tasks = await db.tasks(limit=200)
    failed = await db.outbound(FAILED, limit=50)
    calls = await db.calls_by_message(m.provider_message_id for m in messages)
    by_task = await db.calls_for_tasks(t.id for t in tasks)

    sections = [
        _header(status, counts),
        _failed(failed),
        _tasks(tasks, by_task),
        _messages(messages, calls),
    ]
    return "\n".join(section for section in sections if section)


def _header(status: str, counts: dict) -> str:
    last = counts["last_message_at"]
    when = last.strftime("%H:%M:%S") if last else "never"
    return (
        f'<p class="bar">discord <b>{_e(status)}</b> · last event <b>{when}</b>'
        f" · {counts['messages']} messages"
        f" · {counts['outbound'].get(QUEUED, 0)} queued to send</p>"
    )


def _failed(rows) -> str:
    """First on the page, because it is the only thing here that needs a
    person: nobody delivered these, and the text is here to be copied."""
    if not rows:
        return ""
    items = "".join(
        f"<li><code>{_e(r.text)}</code>"
        f'<span class="why">{_e(r.last_error or "")} · as {_e(r.sender)}'
        f" · task {r.task_id}</span></li>"
        for r in rows
    )
    return f'<section class="failed"><h2>Could not be sent</h2><ul>{items}</ul></section>'


def _tasks(tasks, calls_by_task) -> str:
    columns = []
    for state in TaskState:
        here = [t for t in tasks if t.state == state]
        cards = "".join(
            f'<li><b>{_e(t.type)}</b> #{t.id}'
            f'<span class="why">{_e(_summarise(t))}</span>'
            f"{_calls(calls_by_task.get(t.id, ()))}</li>"
            for t in here
        )
        columns.append(
            f'<div class="col"><h3>{_e(state)} <span>{len(here)}</span></h3>'
            f"<ul>{cards}</ul></div>"
        )
    return f'<section><h2>Tasks</h2><div class="cols">{"".join(columns)}</div></section>'


def _calls(calls) -> str:
    """What the model was asked while working on one task.

    Folded away by default: a prompt carries whatever was in the conversation
    it was assembled from, and a board that unrolls all of them at once is a
    page nobody reads. `_e` is what keeps a reporter's text from becoming
    markup on the way through.
    """
    if not calls:
        return ""
    rows = "".join(
        f"<pre>{_e(c.agent)}"
        + (f" · {_e(c.node)}" if c.node else "")
        + (f" · {c.latency_ms}ms" if c.latency_ms is not None else "")
        + f"\n{_e(c.prompt)}\n\n→ {_e(c.output)}</pre>"
        for c in calls
    )
    return f"<details><summary>{len(calls)} model call(s)</summary>{rows}</details>"


def _messages(messages, calls) -> str:
    rows = []
    for message in messages:
        call = calls.get(message.provider_message_id)
        rows.append(
            f'<li><span class="who">{_e(message.author_name)}</span>'
            f'<span class="when">{message.created_at.strftime("%H:%M:%S")}</span>'
            f"<p>{_e(message.text)}</p>"
            + (
                f'<details><summary>{_e(call.model)} · '
                f"{call.input_tokens} in / {call.output_tokens} out</summary>"
                f"<pre>{_e(call.prompt)}\n\n→ {_e(call.output)}</pre></details>"
                if call
                else ""
            )
            + "</li>"
        )
    return f'<section><h2>Messages</h2><ul class="feed">{"".join(rows)}</ul></section>'


def _summarise(task) -> str:
    """Whatever the task carries that reads as a description."""
    for key in ("summary", "question", "project", "reason"):
        if task.params.get(key):
            return str(task.params[key])
    return ""


def _e(value) -> str:
    """Everything on this page came from a chat message someone else wrote."""
    return html.escape(str(value))


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>friday</title>
<script src="https://unpkg.com/htmx.org@1.9.12"></script>
<style>
 :root {{ color-scheme: light dark; }}
 body {{ font: 13px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace;
        margin: 0; padding: 1.5rem; max-width: 78rem; }}
 h2 {{ font-size: .8rem; text-transform: uppercase; letter-spacing: .1em;
      opacity: .5; margin: 2rem 0 .6rem; }}
 h3 {{ font-size: .75rem; margin: 0 0 .4rem; }}
 h3 span {{ opacity: .4; }}
 .bar {{ padding: .5rem .7rem; background: #8881; border-radius: 5px; margin: 0; }}
 ul {{ list-style: none; margin: 0; padding: 0; }}
 li {{ padding: .45rem .6rem; border-left: 2px solid #8884; margin-bottom: .3rem;
      background: #8881; border-radius: 0 4px 4px 0; }}
 .cols {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: .6rem; }}
 .why {{ display: block; opacity: .55; font-size: .85em; }}
 .failed li {{ border-left-color: #e5484d; }}
 .failed h2 {{ color: #e5484d; opacity: 1; }}
 .feed p {{ margin: .2rem 0; }}
 .who {{ font-weight: 600; }} .when {{ opacity: .4; margin-left: .5rem; }}
 pre {{ white-space: pre-wrap; font-size: .85em; opacity: .8; margin: .4rem 0 0; }}
 summary {{ cursor: pointer; opacity: .5; font-size: .85em; }}
</style></head>
<body hx-get="/body" hx-trigger="every {refresh}s" hx-target="body">
{body}
</body></html>""".replace("{refresh}", str(_REFRESH_SECONDS))
