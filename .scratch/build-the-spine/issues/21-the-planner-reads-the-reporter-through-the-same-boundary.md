Status: ready-for-agent
Blocked by: 20

# The Planner reads the reporter through the same boundary

Decisions: [The Planner](../../domains-plug-in/issues/12-the-planner.md) §3
(core instructions), and the prompt-assembly rule in `docs/DESIGN.md` § What
exists (`friday/sdk/prompt.py`: one assembler, one escaping seam, the trust
boundary stated once and the markers around every word a person wrote).
Source: operator design review, 2026-09-30.

## The problem

Every agent's stable prompt goes through `assemble` and its sections; what a
person wrote reaches the model between `--- BEGIN USER INPUT ---` markers,
escaped, and the system prompt explains that convention once
(`trust_boundary`). The guard is `tests/test_prompt_sections.py`
`test_every_agents_instructions_are_built_by_the_one_assembler`, over the
modules `_prompt_modules` lists.

The Planner is outside all of it (`friday/kernel/spine/planner_prompt.py`,
ticket 11):

- `INSTRUCTIONS` is a hand-written string with no `trust_boundary`, so the
  Planner is never told that what follows contains a stranger's words.
- `_case` puts `intake.request_text` raw under a markdown `## The request`
  heading, no markers, no escaping; memory rows and skill names the same.
- `replan_prompt` puts `signal.found` and `signal.reason` raw — text an
  agent wrote after reading the reporter's logs, which is where injected
  text lands next.
- `_prompt_modules` does not list the module, so the guard cannot see it.

The Planner is the one agent that decides the whole plan, and the one that
reads the reporter with no boundary. It also mixes two shapes: XML sections
in every other agent's prompt, markdown headings here. The mixing is not
itself the defect (a model reads both); the drift is — the docstring of
`plugins/backend/agents/diagnose_prompt.py` records that hand-written prompts
were wrapped after the fact four times already.

The DAG-side twin, `build_reads_input` in `diagnose_prompt.py` (markdown
headings, `report` raw), is reached only from `plugins/backend/graph/
diagnose.py` and goes with the DAG path in ticket 16; not this ticket.

## Goal

- `friday/kernel/spine/planner_prompt.py`:
  - `INSTRUCTIONS` assembled from the sdk builders: `role`, `trust_boundary`,
    `job` (the step types and the rules), `critical_reminder` (fix every
    error, whole plan again). Same content, sectioned.
  - `first_prompt` / `replan_prompt` / `refusal_prompt` assembled from
    sections instead of `## ` headings: the request through `user_input`;
    intake's placement, memory and skills, the action, the agents, the
    current plan and its results, the replan signal each a section whose
    body is escaped by the builder. `signal.reason` / `signal.found` are
    agent output over reporter data and go through `user_input` too.
  - No `Section(...)` built by hand: add the builders the Planner needs to
    `friday/sdk/prompt.py` where none fits (`case`, `action`, `plan` — or
    one generic named-section builder, the operator's call at build time).
- `tests/test_prompt_sections.py` `_prompt_modules` lists
  `spine/planner_prompt.py`; the two guards pass over it.
- A test in the shape of `test_the_extractor_does_not_take_a_reporters_words_raw`
  for the Planner: a request containing `</job>` and a forged end marker
  reaches the model escaped, inside the markers.
- `core.planner` run and reported (the prompt changed): no case worse than
  the baseline after ticket 20.
- `docs/DESIGN.md` § What exists, the Planner paragraph: its prompt is
  assembled like every other agent's.

## Acceptance

- [ ] The Planner's instructions carry `trust_boundary`; every reporter
      word in its opening and replan messages is between the markers,
      escaped (test).
- [ ] `_prompt_modules` includes the Planner; both assembler guards green.
- [ ] `core.planner` reported with the change.
- [ ] `docs/DESIGN.md` corrected.
- [ ] Whole suite green; `code-review` done.
