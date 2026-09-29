Status: done
Blocked by: 06, 10

# The Planner and its plan-shape eval

Decision: [The Planner](../../domains-plug-in/issues/12-the-planner.md) (as amended by 17).

## Goal

`friday/kernel/spine/planner.py`: one core agent, always runs, strong tier
(core constant), budget `(max_turns, tokens)` core constants, toolsets
`core.memory` + `core.skills` only.

- In: `IntakeContext`, the contract, each allowed agent's / toolset's
  name, description, result shape, terminal tools, budget;
  `Action.planning`; on replan: current plan + stored results + reason.
- Gate refusal → errors appended to the same conversation; 2 rewrites (core
  constant) → `HandOver planner_failed` with last plan, all gate errors,
  what it read. Replan → fresh conversation, own 2 rewrites.
- `evals/`: a code-graded plan-shape eval on synthetic fixtures (terminal
  step, agent, toolsets; `brief` not graded), Planner replayed by cassette.

## Acceptance

- [x] Refusal-then-rewrite and `planner_failed` paths tested.
- [x] Eval runs by hand like `run_triage_eval`; `evals/README.md` says when
      it must be run (a Planner prompt change); first numbers reported.
- [x] `CONTEXT.md`: *Planner*, *replan*.
- [x] Whole suite green; `code-review` done.

## Done 2026-09-29

Operator decisions: the eval's "cassette" is fixed memory/skills with a live
model; the Planner's tier is a new `strong` tier (`z-ai/glm-5.3-flash`);
the synthetic cases are committed under `evals/datasets/planner/`.

Decided while building:
- The Planner runs through the `Harness`, not `run_agent` (no terminal tools;
  its toolsets are not the contract's), and gets only the read tools of
  `core.memory` / `core.skills` (`PLANNER_READS`).
- The `Harness` keeps `messages` after an answer, so a refusal continues the
  conversation; every refusal message also repeats the refused plan and its
  errors, since a failed run keeps no messages.
- `PlannerFailed` subclasses `HandOver`; toolsets are graded "at least those
  expected".

First numbers: 6/8 twice — see `evals/README.md` § First numbers.

## Carried

- **To 14:** bind `Planning` (with the `FridayState` and the core toolsets
  wired to the store) and `functools.partial(replan, p)` as `Steps.planner`;
  persist `PlannerFailed`'s `plans`/`errors`/`read` (a `HandOver` dump drops
  them); `PlannerFailed.read` misses a failed run's calls (they are in the
  recorded model calls only).
- **Operator:** `permission-for-what` hit the 10-turn budget on all 3 tries
  in one run, and `trace-too-vague-to-start` never asks — prompt or budget
  work, measured by `core.planner`.
