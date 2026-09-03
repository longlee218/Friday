# 13: The graph engine moves out of `__init__.py`, and two guards start working

**What to build:** `friday/dag/`'s engine lives in a module of its own, so
importing anything from that package stops dragging it in — the rule the
package next door states in its own docstring. Two guards that currently
cannot fire start firing.

**Blocked by:** None (can start immediately)

**Decisions:** none — this is CLAUDE.md's Layout and Verifying-a-change rules

**Status:** ready-for-agent

## Why

Three things found by a standards review of the whole board. None changes
behaviour; each is a rule this repo states and does not keep.

**1. `friday/dag/__init__.py` holds 281 lines of implementation.** CLAUDE.md,
*Layout*: explicit `__init__.py` "is a statement about PEP 420, **not a
licence to put implementation in `__init__.py`** — importing any submodule
runs the parent's `__init__.py` first, so whatever lives there is paid for by
every import of the package." `DAG`, `Node`, `Edge`, `DAGDeps` and
`DAGRunner` all live there. Ticket 09 created `friday/tasks/__init__.py` in
the same board, seven lines, whose docstring states that exact rule — so it
is applied in one new package and breached in the one beside it.

The cost is already being paid rather than hypothetical: seven function-local
imports exist to dodge the cycle that `friday.dag` being both engine and
package creates — `router.py:76`, `router.py:113-115`, and
`pool.py:335/436/501/596/653`. Moving the engine to `friday/dag/engine.py`
lets most of them go back to the top of their files.

**2. `register_dag`'s refuse-to-overwrite guard is dead in production.** It
raises on a duplicate registration because "the second registration silently
winning is the kind that surfaces as 'why is it running the old graph?' a
week later" — but both production callers pop the key immediately before
calling it (`router.py:189` and `195`). Only `tests/test_dag.py` can reach
the raise. CLAUDE.md, *Verifying a change*: "Any guard you added has been
deleted once and watched go red." This one is deleted at every call site that
matters. Either idempotency is the intent — in which case `register_dags`
should clear `EDGE_ROUTER` once and say so — or the guard should be allowed
to do its job.

**3. `_HANDS_OFF` still gates `_fix_bug`.** `friday/dag/api_issue.py:194-219`
matches a word list against the model's own prose before the agent runs,
while CLAUDE.md's approval constraint describes that list in the past tense:
"A word list matched against a model's own prose **was** the gate before
this." Either it is deliberate defence-in-depth behind the real gate — in
which case CLAUDE.md must say so, since it is the file that warns it "goes
stale silently" — or it goes with ticket 07's mechanism.

Smells found alongside, worth folding in while the files are open: the
`checkpoint` closure + `DAGRunner(...)` + `try/except → HandOver` block is
duplicated between `_run_dag` and `_continue_from`; `DAG_SERVERS` is imported
and unused at `pool.py:436`; `Harness._settle`'s `agent` parameter is
`self.agent` at both call sites.

## Acceptance criteria

- [ ] The graph engine lives in `friday/dag/engine.py`; `friday/dag/__init__.py`
      carries a docstring and nothing that runs
- [ ] Every function-local import that existed only to dodge the old cycle is
      back at the top of its file, or its remaining reason is written down
- [ ] `register_dags` states its idempotency once, and `register_dag`'s guard
      can fire — proven by deleting it and watching a test go red
- [ ] `_HANDS_OFF` is either removed or documented in CLAUDE.md as a second
      layer, in the same commit
- [ ] `pool.py`'s duplicated runner/checkpoint block is one function
- [ ] The unused `DAG_SERVERS` import is gone
- [ ] Every assembled prompt is byte-identical before and after — this ticket
      changes no text
- [ ] CLAUDE.md's layout table reflects wherever the engine ends up
