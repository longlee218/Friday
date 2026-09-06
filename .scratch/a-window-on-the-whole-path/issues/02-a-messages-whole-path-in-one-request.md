# 02: A message's whole path, in one request

**What to build:** `GET /api/messages/{provider}/{message_id}/flow` — everything
that happened because of one message, assembled in the store, read at one
instant.

**Blocked by:** None

**Decisions:** D5, D6

**Status:** done

## Why

The operator asked to see "the flow of a run, step by step". The graph cannot
answer that: `build_simple_dag` returns a one-tuple of nodes and
`register_dags` gives every task type that same graph, so `dag_state.trail`
holds one name per task. A graph view would draw one box.

The thing that genuinely has steps is the path a message takes through the
process, and every step of it already leaves a row:

| Step | Where it is recorded |
| --- | --- |
| arrived, from whom, in which conversation | `messages` |
| held by the sensitive-word prefilter | the triage decision's `params.reason` |
| the turn it belonged to | `messages` by author and time |
| classified — type, confidence, the prompt behind it | `messages.decision_type` / `.decision_confidence`, `model_calls` by `message_id` |
| became a task, or a skip | `messages.task_id` |
| parameters extracted, validated | `model_calls` / `tool_calls` by `task_id` |
| asked a question, or handed over | `tool_calls`, `outbox` |
| queued, approved, sent, or failed | `outbox` |

**A message is the spine, not a task (D5).** A task-spined view loses triage —
when the classifier runs there is no task and the call carries only
`message_id` — and loses every `skip`, which is exactly the case worth
interrogating ("why did it ignore this?"). `messages` already carries
`task_id`, `decision_type`, `decision_confidence`, `decision_params` and
`triaged_at`, so the join needs no new column.

**One request, not four (D6).** Joining in the browser means four reads of four
instants of a database being written to; a task can change state between the
second call and the third, and the flow rendered is one that never existed.
`/api/board` is composite for exactly this reason and its docstring says so.

Note what this ticket also fixes on the way past: **`tool_calls` has no JSON
route at all today.** `tools_for_tasks` is called only from the board that
ticket 01 deletes. Without this, ticket 07 of `nothing-runs-unmeasured` — "what
the agent reached for is recorded" — is recorded and unreachable.

## Acceptance criteria

- [~] ~~in one database **instant**~~ — one *request*, which is not the same
      thing and the docstring claimed it was. Several sessions, and SQLite in
      WAL gives each its own snapshot; corrected rather than made atomic, and
      the reason is written where the claim was
- [x] It answers for a message that was **skipped** — no task ever opened — as
      a normal outcome, not a 404 and not an empty object
- [x] It answers for a message the prefilter **held**, and says which word held
      it, since that is a step where no model call exists to explain the gap
- [x] Model calls and tool calls both appear, interleaved in time, each
      carrying what it cost (`input_tokens`, `output_tokens`), how long it took
      (`latency_ms`), which attempt it was, and which node it came from
- [x] Every string goes through `_clean()` like every other response here —
      prompts and `last_error` are the two fields most likely to carry a
      credential. **True of this route and false of the four added in the next
      commit**, which a review caught: `reload`'s `problems` returns a
      `yaml.YAMLError`, and that quotes the source line it failed on. All
      scrubbed now, with an AST test so the invariant is enforced rather than
      remembered
- [x] A message that does not exist is a 404, distinguishable from one that
      exists and did nothing
- [x] Tests cover: the skip path, the held-by-prefilter path, a full path
      through to a sent outbound row, and that scrubbing is applied

## Notes

The store is where the assembly belongs, not the route — `friday/ops/api.py`
converts and scrubs, it does not query across four tables. Expect a new
`Database` method; `calls_for_task`, `tools_for_tasks` and `outbound` already
exist and there is no `tools_for_task` (singular) yet.

Do not add a `dag_state` route. Every graph has one node; when a multi-node
graph exists and somebody has described its steps, that becomes worth a route
and a screen. Building it now is drawing one box.
