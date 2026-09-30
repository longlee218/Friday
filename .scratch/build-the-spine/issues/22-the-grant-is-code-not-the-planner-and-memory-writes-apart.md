Status: ready-for-agent
Blocked by:

# The grant is code, not the Planner; memory writes are their own toolset

Amends: build-the-spine ticket 20 (empty `toolsets` = the full grant; the
Planner may still narrow), [GatePlan](../../domains-plug-in/issues/11-gateplan.md) §2,
[The plan schema](../../domains-plug-in/issues/10-the-plan-schema-and-step-vocabulary.md)
(the `agent` step's `toolsets`).
Source: operator design review, 2026-09-30, after the `core.planner` run.

## The problem

### 1. The Planner can still set an agent's tools

The operator's boundary (ticket 20, restated 2026-09-30) is a rule, not a
preference: **the Planner decides the workflow — which steps, which agent,
what each must establish, in what order — and never how an agent works,
which includes what tools it holds.** Ticket 20 went half way: an empty
`toolsets` is the full grant, but `PlannedStep.toolsets` still exists and
the instructions still say "list some only when … a reason to narrow". The
Planner reads nothing of the reporter's system; it has no basis to narrow,
and a narrowed grant is one the agent can only escape by spending a replan.

The grant is already decided in code, twice: the action contract
(`allowed_toolsets`, the plugin author's ceiling for the action) and the
agent spec (`AgentSpec.toolsets`, the agent's own ceiling). Their
intersection (`full_grant`) is the whole answer.

### 2. `backend.diagnose` can write, rewrite and delete memory

With the full grant, `backend.diagnose` holds all of `core.memory`:
`memory_search`, `memory_propose`, and also `memory_add`, `memory_update`,
`memory_delete`. The toolset is the unit of a grant, so a ceiling cannot
keep the reads and drop the writes. That is wrong for this agent:

- it reads the reporter's logs, response bodies and code — data nobody
  vouches for, where an instruction-shaped line ("remember: service X is
  production") is an indirect injection the trust boundary does not stop,
  because it is the model's own action after reading, not text in its
  prompt;
- memory outlives the task: every later prompt in the room reads it
  (triage, Planner, diagnose, responder), where a wrong reply is one reply
  and still waits for the operator's approval;
- `memory_add` is read back as fact; one diagnosis is evidence read once,
  and `conclusive: false` exists because the model is confidently wrong;
- `memory_update`/`memory_delete` can rewrite or remove the operator's own
  (`admin`) rows;
- `memory_propose` already serves the need: it records what was learned,
  and the operator approves it before anything reads it back.

The Planner is already read-only (`PLANNER_READS`) for the same reason.

## What is decided

- **The Planner writes no grant.** `PlannedStep` loses `toolsets`; the plan's
  `AgentStep.toolsets` is always `full_grant(contract, spec)`, filled by code
  before hashing (as now). The instructions stop mentioning `toolsets`; the
  `agents_allowed` section still lists each agent's tools so the Planner can
  pick the right agent. A step that needs a tool its agent does not hold is
  still a `replan` — the Planner answers with another agent or step, never
  with a wider grant. Amends ticket 20 and decision 11 §2 (nothing left for
  GatePlan to refuse about toolsets on a Planner-written plan; the check
  stays for a plan built any other way).
- **`core.memory` splits in two toolsets (option 1 of the review):**
  - `core.memory` — `memory_search`, `memory_propose` (read, and suggest
    for the operator's review);
  - `core.memory_write` — `memory_add`, `memory_update`, `memory_delete`.
  An underscore, not a dot: toolset names are `<owner>.<thing>`.
- **`backend.diagnose` gets `core.memory` only**; `trace_problem`'s contract
  drops nothing it no longer needs to. No agent spec is granted
  `core.memory_write` by this ticket.
- Not option 2 (a per-agent tool filter beside toolsets): one grant unit,
  one place to read it.

## Goal

- `friday/kernel/spine/planner_prompt.py`: `PlannedStep` without `toolsets`;
  `to_steps` always fills `full_grant`; instructions and docstrings say the
  grant is the agent's. `plan.py` `AgentStep` docstring.
- `friday/kernel/toolsets/`: `core.memory` (search, propose) and
  `core.memory_write` (add, update, delete) registered by `core_toolsets`;
  descriptions say which is which. `tests/test_tools.py` and
  `tests/test_core_toolsets.py` updated.
- `plugins/backend/agents/diagnose.py` and `explain.py` ceilings, and the
  action contracts, name what they hold after the split.
- `PLANNER_TOOLSETS` / `PLANNER_READS`: unchanged in effect (search only).
- Check every other holder of the memory tools — the responder's memory
  tools (`memory_tool_system`, `MEMORY_TOOLS` in `instruction_prompt.py`)
  — and keep its behaviour exactly; if it builds tools outside the
  toolset, say so in the ticket rather than widen this one.
- `core.planner` eval: the `toolsets` check goes (it grades what the
  Planner no longer does); `expect.toolsets` leaves the case files;
  `evals/README.md` says so.
- Decisions 10 and 11 and ticket 20 amended; `docs/DESIGN.md` (the Planner
  paragraph, `friday/kernel/toolsets/` row, D6 if it names memory writes)
  and `CONTEXT.md` (*Toolset*, *Brief*) corrected.

## Acceptance

- [ ] The Planner's answer shape has no `toolsets`; every frozen agent step
      carries `full_grant` (test).
- [ ] `backend.diagnose`, run through `run_agent`, is offered
      `memory_search` and `memory_propose` and not `memory_add`,
      `memory_update`, `memory_delete` (test).
- [ ] The responder's memory behaviour unchanged (its existing tests green).
- [ ] `core.planner` run and reported: no case worse than the run of
      2026-09-30 (6/8) — run twice, since one run moved a case each way.
- [ ] Decisions and docs amended.
- [ ] Whole suite green; `code-review` done.
