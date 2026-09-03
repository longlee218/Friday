# 09: The loop becomes the pool, and workflows/ dissolves

**What to build:** The loop is named for what it does — pull pending tasks,
host their graphs, act on what comes back — and lives in a package of its own.
The package that used to hold a second way of deciding what to do with a task
is deleted. The one new rule in this spec gets the loudest test in the suite.

**Blocked by:** 05, 06, 07, 08

**Decisions:** D5, and the invariant

**Status:** done

## Why

This is the contract step. Tickets 01–08 each moved something out of
`friday/workflows/` while leaving the package importable, so every one of them
could land green on its own. Folding the deletion into whichever of them
happened to land last would make that ticket's green depend on landing order,
which is not a property anyone can check while writing it.

What the loop actually owns is the task's lifecycle: stand down when the
operator answered, announce what nobody can act on, host the graph, turn its
outcome into rows. That is a pool engine, not a workflow, and it does not
belong inside the graph package either — hosting a graph is one of the four
things it does.

The invariant is new to this codebase and has no history of being enforced:
**only Responder-family agents produce text that reaches a reporter.** It is
the line most likely to be eroded quietly by a future ticket, because eroding
it breaks nothing — it just puts a different voice in the operator's mouth.

## Acceptance criteria

- [x] The loop lives in a `tasks` package as the pool, and does four things:
      stand down, announce needs-human, host the graph, act on the outcome
- [x] `friday/workflows/` is gone and nothing imports it; whatever was left in
      it lives beside its one caller
- [x] Only Responder-family agents can produce text that reaches a reporter,
      pinned by a test that fails loudly and says why
- [x] The composition root builds the pool by its new name and is otherwise
      unchanged — it still constructs adapters and starts tasks, and reads no
      agent's knobs
- [x] Every assembled prompt is byte-identical before and after, captured
- [x] CLAUDE.md and CONTEXT.md describe the result in this same commit — the
      layout table, the architecture constraints that named `workflows/`, and
      the vocabulary entries for the pool and for hand-over

## What it came to

`friday/workflows/runner.py`'s `WorkflowRunner` is `friday/tasks/pool.py`'s
`Pool` now — same four things every pass (stand down, announce, host the
graph, act on the outcome), same methods, moved rather than rewritten.
`friday/tasks/__init__.py` stays empty, matching every other package that
pays for its `__init__.py` on every import.

`friday/workflows/__init__.py` had three things in it, not one, and each got
its own home rather than all three following the loop:

- `prepare()` and its helpers (`_fill`, `_missing`, `_problems`, `_question`,
  `_question_from_clarify`, `_ASKED_AS`) and `plan_by_required_parameters()`
  moved into `friday/dag/prepare.py`, beside `prepare_node` — their one
  production caller, directly (`plan_by_required_parameters`) or through the
  node it builds (`prepare`).
- `PARAMS` and `MODEL_AUTHORED` moved into `friday/domain/models.py`, beside
  the `Params` dataclasses they describe — both had more than one caller
  (`friday/dag/router.py`, `friday/triage/`, `friday/extraction/`, the pool
  itself), so "beside its one caller" pointed at the domain rather than at
  any of them.

No import cycle needed breaking either way: `friday/dag/prepare.py` importing
`friday.extraction` for `Clarify`/`extract` is the same shape
`friday/workflows/__init__.py` already had, and `friday/extraction/`'s
`MODEL_AUTHORED`/`PARAMS` imports move to plain module-level imports from
`friday.domain.models` — the deferred, cycle-breaking import they used to
need was only ever needed against `friday.workflows`, never against the
domain.

The invariant — only Responder-family agents produce text that reaches a
reporter — is pinned in `tests/test_repo_hygiene.py` two ways at once:
`Family.RESPONDER` may only be named inside `friday/responder/` and in
`friday/dag/prompt.py`'s one conditional, and that conditional itself is
walked node by node to confirm `compose_reply` is the only one wired to it.
Mutation-tested by flipping `dag/prompt.py`'s `if node == "compose_reply"` to
always pick `Family.NODE` and watching the second half fail.

`test_the_graph_engine_does_not_import_vocabulary_from_the_loop` — pinned
against `friday.workflows` specifically, and about to be vacuously true the
moment that module stopped existing — became
`test_the_graph_engine_only_imports_vocabulary_from_the_domain`: `Action`,
`Ask`, `HandOver` and `Reply` may only be imported into `friday/dag/` or
`friday/tasks/` from `friday.domain.actions`, from now on, not just "not
from the retired package". Mutation-tested the same way, against a real
second import — first caught the test only walked `friday/dag/` while its
own docstring claimed the pool too (a code-review finding), so it now walks
both packages, and the mutation was re-run against `friday/tasks/pool.py`
specifically to confirm that half was not vacuous either.

Two tests broke on the move for reasons worth recording rather than just
fixing quietly: `test_validate_is_only_invoked_from_one_call_site`
(`tests/test_validation.py`) hard-coded the one allowed caller as
`friday/workflows/__init__.py`, which is where `_problems`'s call to
`validate()` used to live — updated to `friday/dag/prepare.py` and
mutation-tested against a real second call site. And `test_dag.py`'s several
dozen `from friday.workflows.runner import WorkflowRunner` and
`from tests.test_workflow_runner import make_task` lines needed the same
mechanical update the rename made everywhere else.

`tests/test_workflow_runner.py` and `tests/test_workflow_api_issue.py` are
`tests/test_pool.py` and `tests/test_dag_prepare.py` now, `git mv`'d so the
history follows, matching the module each now tests.

CLAUDE.md's layout table drops the `friday/workflows/` row and adds
`friday/tasks/`; its status section and the two `WorkflowRunner` mentions in
the architecture constraints (`_plan`, `decide_pending_action`) follow the
rename. CONTEXT.md's "Workflow" heading splits in two: "Pool" (the loop) and
"Deciding an action" (`prepare` + the graph, what the old heading was mostly
about already), plus a new "Hand-over" entry — distinguishing the `Action` a
node produces from "Handled by the operator", a different fact about a
different state, that the two names read confusingly close to each other
before this.

Every assembled prompt (every `dag/prompt.py` node, with and without a
persona and a skills catalogue, the responder, extraction, triage) captured
before and after with an ad-hoc script and diffed byte-identical — expected,
since no prompt-text file was touched, only where `PARAMS`, `MODEL_AUTHORED`
and the fill-and-validate mechanism live.

631 tests pass (630 before this ticket, +1 for the new invariant test).
