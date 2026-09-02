# 03: prepare is node 0, and the checkpoint has two tiers

**What to build:** Reading everything the reporter has said and checking it
becomes the first node of the graph rather than a gate in front of it, and the
graph's saved work is keyed on what that node produced. A "cảm ơn anh" no
longer costs an investigation; a correlationId that arrives late does.

**Blocked by:** 01, 02

**Decisions:** D2, D7, D8

**Status:** done

## Why

The gate in front of the graph and the graph itself are the same mechanism
under two names, and keeping them apart costs the one thing the graph exists
to buy. The fingerprint today is taken over the task's stored parameters
*before* extraction has run on the new message, so the state is judged against
inputs that are one message out of date. Hashing the reporter's text instead
would swing the other way: any new sentence, thanks included, discards a
finished investigation.

Node 0's *output* is the honest key. It is computed from everything said so
far, and it changes exactly when what the graph knows changes.

## What the two tiers mean

| Reporter sends | node 0 | nodes 1+ |
|---|---|---|
| "API lỗi" | runs; no id → ask | never reached |
| "cảm ơn anh" | runs (new text); params unchanged | state kept, nothing re-runs |
| "cid là abc…" | runs; params changed | state discarded, investigation re-runs |

Node 0 is never checkpointed — there is new text, so it must be read. Nodes 1+
are checkpointed against its output.

## Acceptance criteria

- [x] The graph's entry node extracts from everything the reporter has said
      and validates the result; nothing outside the graph does either
- [x] Node 0 runs on every pass, and is never written to the stored state
- [x] The stored fingerprint is a hash of node 0's output; nodes 1+ are
      discarded exactly when it changes and kept when it does not
- [x] The three-message table above holds end to end, driven at the pool's
      `run_once` seam
- [x] Parameters filled in by node 0 are still written back to the task, so
      the board shows what the system acted on
- [x] Extraction still runs before validation, so a hallucinated field is
      caught rather than believed
- [x] Exempt from the byte-identity rule — D2 and D7 change behaviour — and
      the ticket says so where a reader would otherwise look for a capture
- [x] Each guard is removed in turn and watched go red, per tickets 34–41

## What it came to

`api_issue`'s graph gets a real `Node("prepare", _prepare)` as its entry —
`build_api_issue_dag()`'s first node, so `dag.entry` defaults to it. `_prepare`
reuses `friday.workflows.prepare` (extraction + validation, unchanged) rather
than re-implementing it, and writes the filled parameters back to the task
itself — the same write-back `_remember` used to do externally, now inside
the node whose output it belongs to. Downstream nodes read `state["prepare"]`
through a `_params(state)` helper instead of `deps.task.params`, which used to
be a snapshot from *before* this pass's extraction ran.

`_run_dag` runs node 0 once, upfront, outside the checkpoint: there is no
fingerprint to load state *by* until it has produced one. If it returns an
`Ask` (or any `Action`), the graph never starts — same as today's
deterministic path. Otherwise its output — `asdict()`'d if it is a dataclass,
used as-is if already a mapping (`_prepare_material`) — becomes the
fingerprint, and only *then* does stored state get loaded, filtered by that
fingerprint exactly as before. What gets checkpointed (`_checkpointable`)
excludes node 0's own result, both on an ordinary checkpoint and on a
mid-graph pause.

`_plan` no longer prepares before checking `dag_for` — a graph type's own
entry node does it now; the deterministic (non-graph) path is unchanged,
since ticket 04 is what turns `access_request`/`doc_question` into graphs.

The bulk of the diff is tests. `friday/dag/api_issue.py`'s and
`friday/workflows/api_issue`-adjacent tests drove the graph directly with a
`deps()`/`run()` helper that had no `db` at all — `prepare` needs one now, so
they get a minimal stub (`original_text_for` → `None`, which skips extraction
entirely and lets `prepare` validate exactly the params the test already
built). `test_dag.py`'s synthetic-graph tests that were *specifically* testing
"a completed node does not run twice" via what became node 0 needed a real
prepare-shaped node 0 prepended, so the node under test moved to node 1 and
kept testing what it always tested. One of them
(`test_answering_the_question_re_runs_the_nodes_that_asked_it`) initially
looked green without that rewrite — its node happened to already be node 0,
so it passed for the wrong reason (node 0 always reruns unconditionally, not
because the fingerprint changed). A mutation test caught it: breaking the
fingerprint into a constant left the test green. Rewritten with an explicit
node 0, the same mutation turns it red.

New: `test_the_three_message_table_holds_end_to_end` drives D7's worked
example against the *real* `api_issue` graph (not a synthetic stand-in) at
the pool's `run_once` seam — "API lỗi" asks and the graph never starts,
"cảm ơn anh" asks again and still never starts it, the correlationId finally
makes it traceable and the graph runs for the first time.

Two mutation tests, both directions of D7: a fingerprint that never changes
(caught by `test_state_survives_a_pass_that_changed_nothing`) and one that
always changes (caught by `test_answering_the_question_re_runs_the_nodes_that_asked_it`
after its rewrite) both turn the suite red.

613 tests, up from 610 before ticket 08 (which added 2) — 1 net new here.
`friday/*/prompt.py` untouched throughout.

Left for ticket 09: CLAUDE.md's "a graph checkpoints after every node" is now
imprecise (node 0 excepted) — the board's own plan assigns the CLAUDE.md/
CONTEXT.md sweep to the final ticket, not to each intermediate one, so it
stays as it is until then. `friday/workflows/runner.py` also has one
pre-existing unused import (`timezone`) noticed but not touched — it predates
this ticket.
