# 13: A long graph must not hold the pool

**What to build:** Bounded concurrency in `Pool.run_once`, configured in
`config.yaml`, default small.

**Blocked by:** nothing. **Decisions:** finding B.
**Status:** done

## Why
`run_once` awaits each task in turn. Every graph so far finishes in seconds;
`api_issue` is bounded at five minutes, and for that long nothing else is
asked, drafted or handed over.

## Verify
- A slow graph and a one-node graph in one batch: the second finishes first.
- One task is never acted on twice concurrently.

## Done
`Pool.run_once` now works the batch side by side under an
`asyncio.Semaphore(concurrency)` instead of awaiting each graph in turn, and
still returns only when everything it took has finished, so `_stand_down`
runs before the batch and `_raise_hands` after it exactly as before. A
`_taken` set on the pool holds every task id a pass has taken and not
finished: a task stays `pending` for as long as its graph runs, so a second
pass that starts meanwhile skips it rather than acting on it again. The knob
is `workflows.concurrency` in `config.yaml`, default 2, passed through
`Pool.build`; below 1 is refused at load (`ConfigError`).

Tests (`tests/test_pool.py`, `tests/test_config.py`): a slow graph first in
the batch that cannot finish until a one-node graph has — the quick one
finishes first; four tasks never run more than the bound at once; a second
pass while a graph is held does not enter it (`entered == 1`); `Pool.build`
passes the configured bound on; default 2, 0 and -1 refused.

Guards reddened, each deleted once and restored: the `_taken` filter (the
twice test times out), the semaphore (bound test sees 3/4), a sequential
batch (the slow/quick test times out), the `< 1` check at load, and the
`concurrency=` line in `Pool.build`.

Suite: `1 failed, 1238 passed, 1 skipped` — the one failure is the known
baseline `test_doc_paths_resolve_to_existing_files`.

**What this does not do.** `run_forever` still awaits each pass, so a task
that arrives *while* a five-minute graph runs waits for that pass to end;
the other slot only serves tasks already in the batch. Letting passes
overlap would run `_stand_down` against a task whose graph is mid-flight —
it moves the task to `handled_by_operator`, and the graph's own
`move_task` then raises `IllegalTransition` — which is a change to stand-down
semantics this ticket was told to keep. It is the next step if `api_issue`
actually runs long (ticket 11's per-node bounds make that measurable).
A graph that *raises* out of `_act` (not a graph failure, which `_run_dag`
already turns into a hand-over — a store error) now leaves its siblings
running to completion rather than never starting them.

No code-review subagent was run: this ran as a subagent without one to
spawn. The orchestrator owes that review.

## Review fixes
A review of the first commit found that working tasks side by side put two
runs of one `Harness` in flight at once — one extractor per type and one
responder serve every task — and the harness kept per-run state on the
instance: the `LogHooks` on the shared agent (a second run replaced the
first's, so a call was written down under the other task, or crashed
building its `ModelCall`), and `last_error`/`refusal`/`unfit`, which a
second run cleared as it began. `Harness._settle` now takes a per-harness
`asyncio.Lock` (`_one_run`) for the whole run; the flags stay safe to read
after the run returns because nothing awaits between the release and the
caller's read. Tasks of different types still run side by side; two of one
type take turns at the model. The wait for the lock is outside
`timeout_seconds`, and the `config.py` and harness comments that justified
the timeout by "the pool works one at a time" now say what is true.

`Database.memory_add`'s docstring said its count-then-insert race on
`MEMORY_PER_CHANNEL` was unreachable because the pool drained tasks one at a
time — the loop this ticket deleted. The count and insert now run under an
`asyncio.Lock` on the `Database`, which also covers the reaction handler
resolving a candidate from the gateway while a responder writes.

Tests: two concurrent runs of one harness each write down their own call
under their own task; a failed run's `last_error` survives a second run
starting while the first writes its record; two writes racing for a
channel's last memory slot land exactly one. All three were red before the
fix, and red again with each lock removed, then restored.

Suite: `1 failed, 1241 passed, 1 skipped` — the known baseline
`test_doc_paths_resolve_to_existing_files`. No code-review subagent was run
on these fixes either (no subagent to spawn from here); still owed.

## Docs owed
- `CLAUDE.md`'s Layout row for `friday/tasks/` ("The pool: pulls pending
  tasks and hosts their graphs") could say it hosts up to
  `workflows.concurrency` of them at once, and that one task is never taken
  twice while its graph runs.
- `CLAUDE.md`'s Harness paragraphs could say one run of a harness happens at
  a time (`Harness._one_run`), which is what makes `last_error`, `refusal`
  and `unfit` safe to read on the instance now the pool runs tasks side by
  side.
