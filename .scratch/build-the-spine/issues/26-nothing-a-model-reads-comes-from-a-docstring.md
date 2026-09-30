Status: ready-for-agent
Blocked by: 23

# Nothing a model reads comes from a docstring

Backlog (operator, 2026-09-30): "không được phép sử dụng docstring để làm
prompt, mô tả hoặc tất cả những cái gì khác, mọi thứ phải khai báo thật
tường minh". Tickets 23–25 build their tools this way; this ticket moves
every other tool and schema onto it and adds the guard. Blocked by 23,
which introduces the declaration shape.
Amends: build-the-spine tickets 08, 09, 10, 20; `friday/sdk/toolset.py`.

## The problem

Today a model is taught through docstrings in four ways, and a docstring is
prose nobody treats as an interface:

1. **Tool descriptions.** Pydantic AI reads a tool's docstring as its
   description and the google-style `Args:` block as each parameter's
   description (`harness.tool`, `friday.sdk.toolset.tool`). Numbers in that
   prose drift from the constants the code enforces: `read_log`'s says
   "Thirty days is the most" beside `MAX_MINUTES_BACK`
   (`plugins/backend/toolsets/logs.py:67`, `:482`). `memory.py` patches its
   docstrings with `__doc__.replace("{RESULTS}", …)` — a workaround, not a
   rule.
2. **Terminal tools.** `ask_reporter`, `hand_over`, `replan`, `retriage`
   (`friday/kernel/harness/run_agent.py:55–95`) — `replan`'s docstring
   carries the ticket 20 rule for when to replan — and the harness falls
   back to `fn.__doc__` for a terminal's description
   (`friday/kernel/harness/harness.py:352`).
3. **Answer schemas.** A result dataclass's class docstring becomes its JSON
   schema description: `PlannedStep` and `PlanAnswer`
   (`friday/kernel/spine/planner_prompt.py`) teach the Planner its answer
   shape through docstrings; `Diagnosis` and the other results likewise.
4. **Generated `__doc__`.** `friday/kernel/domain/triage.py:83` and
   `friday/kernel/extraction/answer.py:128` assign `__doc__` to build a
   schema description.

A reader editing a docstring does not know they are editing a prompt, and a
test that passes proves nothing about what the model was told.

## What is decided

- **A docstring is for a reader of the code, never for a model.** Every
  text a model reads about a tool or a schema is declared as a value:
  - a tool's description, rendered by a function from the constants it
    enforces and the tool names it points to (Claude Code's `prompt.ts`
    pattern), passed as `description=`;
  - each parameter's description, declared beside the parameter
    (`Annotated[..., Field(description=...)]` or an explicit schema), never
    parsed from `Args:`;
  - per-run text (the room's repositories, services) through `prepare=`;
  - semantic checks in `args_validator=`;
  - each answer type's description and each field's description declared
    explicitly (fields already use `metadata={"doc": ...}`; the class-level
    description joins them), never a class docstring.
- **The plugin SDK says the same.** `friday.sdk.toolset.tool` /
  `ToolSpec` take an explicit description and parameter descriptions; a
  plugin tool without them is refused at boot, like a toolset without a
  description is today.
- **The guard is the rule.** A test builds every tool definition and every
  output schema any agent is offered — core, plugin, terminal, answer —
  once as is and once with every function's and class's `__doc__` set to
  `None`, and requires the two to be identical. A second test requires
  each constant a description names to appear in the rendered text.

## Goal

Move onto explicit declarations:

- core: `core.memory`, `core.memory_write`, `core.skills`,
  `core.workspace` (its tools come from pydantic-ai-harness `FileSystem`:
  override their descriptions explicitly, or record why they cannot be);
- backend: whatever of `backend.logs`/`backend.code`/`backend.docs`/
  `backend.db` survives 23 and 25;
- the terminal tools and the harness's `__doc__` fallback;
- every answer type an agent returns (`PlanAnswer`, `PlannedStep`,
  `Diagnosis`, the triage and extraction schemas), and the two generated
  `__doc__` assignments;
- `plugins/backend/graph/` only if it still exists (16 deletes it).

The docstrings stay, rewritten as notes for a reader where they now speak
to the model.

## Acceptance

- [ ] The `__doc__ = None` guard passes over every tool and schema offered
      to every agent, and fails when one description is moved back into a
      docstring (watched red once).
- [ ] No `__doc__` assignment or `fn.__doc__` read remains in `friday/`
      (test).
- [ ] A plugin tool without an explicit description is refused at boot
      (test).
- [ ] `core.triage`, `core.planner` and `backend.trace_problem` evals run
      and reported: the text the model reads moved, so the numbers are
      re-measured.
- [ ] `docs/DESIGN.md` states the rule under load-bearing rules;
      `CONTEXT.md` names the declaration; tickets 08/09/10/20 amended.
- [ ] Whole suite green; `code-review` done.
