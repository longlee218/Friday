Status: ready-for-agent
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

- [ ] Refusal-then-rewrite and `planner_failed` paths tested.
- [ ] Eval runs by hand like `run_triage_eval`; `evals/README.md` says when
      it must be run (a Planner prompt change); first numbers reported.
- [ ] `CONTEXT.md`: *Planner*, *replan*.
- [ ] Whole suite green; `code-review` done.
