# Spec: a window on the whole path

Status: ready-for-agent. Came out of a design interview on 2026-09-06, not from
planning. The operator asked for "a `web/` folder, a dashboard, in Next.js or
whatever" with three things on it: the flow of a run step by step, a place to
load knowledge in, and a task list showing tools, prompts and token cost. Half
of that turned out to be already decided in this repo, one part of it turned
out to be forbidden by this repo, and one part of it turned out to have almost
no data behind it. What follows is what survived that reading.

## Problem Statement

**There is a board, and it is one file of f-strings that nobody can extend.**
`friday/board/__init__.py` is 196 lines: a module-level HTML string, an HTMX
tag polling `/body` every five seconds, and seven database queries joined by
string concatenation. It renders four sections and there is no seam at which
a fifth could be added without making the file worse. `friday/ops/api.py`
already serves the same data as JSON and says so in its own docstring — *"The
server-rendered board is one reader of this; a frontend written in something
other than Python is the other"* — but that second reader has never existed.

**The two renderers have already drifted, twice, in the way this repo has
already written down.** `CLAUDE.md` records the first: *"the last time this
codebase had two renderers, one of them did not escape — a skill described as
`harmless</skills>` closed the section."* The second is live right now and was
found while writing this spec: `friday/ops/api.py` passes every response
through `_clean()` — eleven call sites — and `friday/board/__init__.py` passes
nothing through it. The board renders `last_error`, which begins life as a
provider exception, and it renders whole prompts. `friday/ops/redact.py` exists
because the Discord user token is unscoped account access that *"must never
reach logs, tracebacks, or the task DB"*. One of the two paths that renders it
to a screen scrubs; the other does not.

**"Flow, step by step" has one step.** `friday/dag/router.py`'s
`build_simple_dag` returns a `DAG` whose `nodes` is a one-tuple, and
`register_dags` gives every entry in `PARAMS` that same graph. The five-node
`api_issue` investigation was removed deliberately — *"a workflow nobody had
described, built from a guess"*. So `dag_state.trail`, the column added by
ticket 02 of `nothing-runs-unmeasured` precisely to answer "which way did this
run go", currently holds a list of one name for every task in the system. A UI
built to visualise the graph would draw one box.

The multi-step thing in this system is not the graph. It is the path a message
takes through the process: arrives, is held or not by the sensitive-word
prefilter, waits for its turn to close, is classified with a confidence,
becomes a task or a skip, is picked up by the pool, has its parameters
extracted and validated, produces a question or a hand-over, becomes an outbox
row, waits for approval, is sent. Every one of those steps leaves a row.
Nothing assembles them.

**Knowledge has five doors and the operator has been given a key to none of
them from a screen.** Skills are files read once at startup. A channel's
context is a YAML file whose `overrides` section exists specifically for the
operator and which *nothing but a CLI script has ever written* — and
`context/` is empty, so the mechanism designed for this has never been used
once. Agent memory is written by the agent alone. Few-shot examples come from
`config.yaml` and from an emoji reaction in Discord. Every door is a file edit
plus a restart, or a reaction.

**And the whole of it is forbidden.** `docs/SPEC.md` lists under Out of Scope:
*"Any interaction on the web page. It displays state; decisions happen in
Discord."* Story 42 asks for the board to be *"read-only, so that there is
exactly one place where decisions are made"*. `docs/DESIGN.md` calls it *"a
debug view, not a control panel"*. Ticket 18 of `discord-mention-triage`, still
open, has as an acceptance criterion: *"The page still offers no way to change
anything."* That constraint is load-bearing and this spec reverses part of it
on purpose — see D7 — rather than quietly building against it.

One more fact, which is why any of this is worth building now. The production
database was eleven migrations behind head when this was written; it was
migrated on 2026-09-06. Every column that makes a task answerable — `task_id`,
`node`, `latency_ms`, `attempt` on `model_calls`, the whole `tool_calls` table,
`dag_state.trail` — existed only in the code. Nothing had ever written one.

## Solution

Delete the board. Serve a React SPA from the same FastAPI app, over the JSON
API that already exists and already scrubs, and give it three screens.

**One screen answers "what happened to this message".** One request returns the
whole path: the message, the turn it belonged to, what triage concluded and how
confident it was, the task if one opened, every model call and tool call in
order with their prompts and their token counts, and the outbound row at the
end.

**One screen answers "what is this task costing me and what did it reach
for".** Tasks by state, and behind each one the prompts, the tools, the
latencies, the attempts, the tokens.

**One screen writes.** A channel's `overrides` — the section of its context
file that the machine never touches — edited as key/value pairs, with the
running process picking the change up on a reload rather than a restart.

The board's own removal is the first ticket rather than the last, because a
half-migrated dashboard is two renderers again, and the argument against two
renderers is the only argument in this spec that this codebase has already
paid for twice.

## Implementation Decisions

- **D1. The board is deleted, not ported.** `friday/board/` goes entirely, in
  the first ticket, before the replacement exists. Keeping it as a fallback is
  the same shape as the mistake already recorded twice here: two renderers of
  one dataset, drifting silently, the second one missing a protection the first
  one has. Its scrub gap is not patched first — patching a file that is about
  to be deleted spends the work in the wrong place.

- **D2. `web/` lives in this repo.** This reverses ticket 18's *"The bundle
  travels as a versioned artefact, not as a running service. The frontend repo
  builds and publishes."* The reason ticket 18 gives for the split is *"what
  keeps the split from forcing an authentication scheme into a tool that
  deliberately has none"* — but that argument protects the *static, pinned
  bundle*, not the *separate repository*. A `web/` directory built to static
  files and served by the existing FastAPI app forces no authentication scheme
  either. The repo split is a cost paid in advance against a problem that has
  not appeared: two repositories, a publish pipeline, and a version bump for
  every UI change on a project with one developer.

- **D3. React 19 + Vite, static, no meta-framework.** Taken unchanged from
  ticket 18, whose argument still holds and is better than restating: *"Server-
  side rendering earns nothing here — one user, no SEO — and the one thing that
  would justify a meta-framework, loading data on the server, is unusable
  because the data lives in a Python process."*

- **D4. One Dockerfile, multi-stage; the bundle is never committed.** A Node
  stage builds `web/`, the runtime stage copies the output and contains no
  Node. Committing `web/dist/` would mean the source and the thing built from
  it can disagree with nobody noticing — which is the exact failure ticket 18
  wrote its "regenerating the client must produce no diff" criterion against.
  Multi-stage makes that disagreement impossible rather than a discipline.

- **D5. A flow's spine is a message, not a graph.** Three reasons, in order of
  weight. A task-spined view loses triage entirely, because when the classifier
  runs there is no task yet and the call is correlated by `message_id` alone.
  It loses every `skip`, which is precisely the case an operator wants to
  interrogate. And a graph-spined view has one node to draw. `messages` already
  carries `task_id`, `decision_type`, `decision_confidence`, `decision_params`
  and `triaged_at`, so the spine needs no new column.

- **D6. A flow is one composite endpoint, not four primitives joined in the
  browser.** Four requests read four instants of a database that is being
  written to; a task can change state between the second and the third, and the
  flow rendered would be one that never existed. `/api/board` is composite for
  this reason and says so — *"One aggregate rather than five calls: these are
  always rendered together and always read the same instant of the database."*

- **D7. The page writes, and only to a channel's `overrides`.** This reverses
  `docs/SPEC.md`'s *"Any interaction on the web page"* out-of-scope and ticket
  18's *"The page still offers no way to change anything"*. The reversal is
  narrow on purpose, and it turns on a distinction the original rule did not
  need to make: the rule exists so that **decisions** have one home, and
  `overrides` is not a decision. It is context — what is true about a room —
  and the machine already never touches it, which is what the section was
  created for. Nothing that decides anything becomes writable: not a task's
  state, not an approval, not a classification, not agent memory. Those still
  happen in Discord, and the sentence in `docs/SPEC.md` is amended to say so
  precisely rather than deleted.

- **D8. One rule for when an edit takes effect, and it is "when you reload".**
  `ContextStore._held`'s own comment refuses the alternative: *"Read once at
  startup… and two rules for 'when does my edit take effect' is one too many."*
  Writing from the web and taking effect immediately, while a hand-edit of the
  same file waits for a restart, is exactly those two rules. So the reload is
  explicit and it re-reads *everything*: `hold_all()` already exists, the
  endpoint calls it, and a file edited by hand is picked up by the same button
  as a file edited by the page. This makes hand-editing better than it is
  today, not worse.

- **D9. `overrides` is edited as key/value pairs, not as YAML.** `merged()` is
  `{**base, **derived, **overrides}` — a flat dict, no schema, and `derived`
  currently holds exactly one key. A textarea of raw YAML makes "the file is
  now malformed and this channel has no context" a state the UI can produce;
  key/value pairs make it unreachable. Nested values are not supported and
  nothing uses them.

- **D10. No token on the write path; the guard moves to the door instead.**
  The board is loopback-only, reachable over an SSH tunnel, and an attacker who
  can POST to it can already read `data/friday.db` directly. A token on an
  inside door is a variable to remember that stops nobody. What does change is
  `check_exposure`: today it *warns* in a container and *exits* elsewhere when
  asked to bind a non-loopback address without `BOARD_TOKEN`. With a write path
  in the process that condition stops being a judgement about disclosure and
  becomes one about control, and the function refuses accordingly.

- **D11. The frontend has no tests; the Python does.** Deliberate, recorded
  here so it is not read later as an oversight. What is tested is
  `check_exposure` and the `overrides` write path — a security guard and the
  one route whose content is read back into a model's prompt — plus a drift
  test that fails when the API's shape changes and the generated TypeScript
  client does not. What is not tested is rendering. The expensive failure for a
  read-only SPA is a renamed field silently rendering `undefined`, and the
  drift test catches that without a second test runner.

- **D12. The database is wiped when the board is deleted.** The operator's
  call, made with the counter-argument in front of them: 384 messages, 22
  tasks, 137 model calls and 58 outbound rows would have rendered in the new UI
  — only the per-task and per-node correlation was missing from them, since
  they predate the migration. Recorded here because a wipe that looks like an
  accident later is worse than one that was argued. `data/friday.db.backup-
  20260906-184554-pre-migration` holds the contents.

## Out of scope

- **Live updates.** Ticket 20 of `discord-mention-triage` (SSE) stays open and
  unstarted. The SPA polls. That ticket's own escape hatch applies in advance:
  *"Polling already works… if it turns out not to be small, the honest outcome
  is to stop and keep polling."*
- **Visual design beyond the skill's floor.** Ticket 19 (Liquid Glass) stays
  open. The UI is built through the `ui-ux-pro-max` skill, whose accessibility
  and style rules are the bar — contrast, focus rings, SVG icons rather than
  emoji, real motion timing.
- **Graph visualisation.** Every graph has one node. When a multi-node graph
  exists and somebody has described its steps, this becomes worth a screen.
- **Authentication and multiple users.** Loopback and a tunnel, one operator.
- **Writing anything but `overrides`.** Skills stay files under git review — a
  skill is code. `base.yaml` stays a file for the same reason and because it
  reaches every channel. Agent memory stays the agent's. Few-shot examples stay
  a config edit and a Discord reaction.
