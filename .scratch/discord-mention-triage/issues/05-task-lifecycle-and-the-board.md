# 05: Task lifecycle and the board

**What to build:** Tasks move through defined states, and a web page shows what the
system is doing — tasks by state, messages as they arrive, model calls with their
prompts, and anything that failed to send.

It is a **debug view**, not a control panel. It displays; every action happens in
Discord. That is what lets it run without auth.

**Blocked by:** 04, 12, 13 — all done

**Status:** done

- [x] Tasks are moved by named operations; no caller writes a state value directly
- [x] An illegal state change is rejected rather than silently applied
- [x] Tasks left mid-flight by a restart are recovered on startup rather than stranded — *nothing can be mid-flight: see below*
- [x] The page lists every task grouped into its five states
- [x] The page shows messages as they arrive, and the model calls made about them with their prompts and tool calls
- [x] The page shows outbound rows that failed to send, including their text, so it can be copied and sent by hand
- [x] The page updates without a manual refresh
- [x] The page shows connection health and the time of the most recent captured event
- [x] The page offers no way to change anything — it displays state only


## Delivered

`friday/tasks.py` holds the states and the legal moves between them. The names
were previously written down in four modules and the graph in none, so a typo
was a task in a state nobody polls — invisible, and indistinguishable from
correct operation. `Database.move_task` refuses an illegal move rather than
writing it; moving a task to where it already is stays legal, because two
deliveries of the same follow-up must not crash a runner.

`review` is declared now although only ticket 06 uses it, so the graph is
complete rather than growing a state at the moment it is first needed. `done`
is terminal: a stray follow-up cannot reopen work someone deliberately closed.

The board is a **debug view**. Failures that need a person come first — an
outbound row nobody could deliver, with its text ready to copy and the error
that stopped it. Then tasks by state, then the message feed, each message
carrying the model call made about it behind a `<details>`: the prompt, the
tool call, the token counts. It polls itself with HTMX, and everything on it is
escaped, because every string on that page was written by someone else in a
chat.

## On "recovered on startup rather than stranded"

Nothing can be mid-flight. That criterion was written for the earlier design,
where a suspended coroutine held a task's position and a restart lost it. Under
the design that actually shipped every state is durable and every runner polls,
so a restart resumes by definition — a `pending` task is picked up, an
untriaged message is picked up, a `queued` outbound row is picked up.

Startup logs what it is holding instead of pretending to recover it, so a
restart does not read as a fresh start.
