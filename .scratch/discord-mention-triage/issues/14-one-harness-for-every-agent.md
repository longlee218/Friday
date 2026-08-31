# 14: One harness for every agent

**What to build:** Adding an agent stops meaning copying the last one. An agent is
declared — instructions, tools, output shape — and something else knows how to run
it. Proven by the responder being the second agent to go through it, with triage
unchanged in behaviour.

**Blocked by:** 04. **Do not start before a second agent exists.** One agent is a
hypothetical seam; two is a real one, and building this against triage alone means
guessing at what varies.

**Status:** done

`friday/triage/__init__.py` currently declares the task parameter types, the tools,
the prompt, the model wiring, the error policy and the regex hygiene in one 310-line
module. The responder needs the model wiring, the hooks, the caps and the error
policy — and under the present shape it can only get them by copying them.

There is also a wrong-direction dependency: `friday/workflows/` imports
`ApiIssueParams` from `friday.triage`, reaching for the task's parameter schema
through the agent that happens to fill it in. Those types belong in `friday.models`.

- [x] A new agent is added by declaring what makes it different, not by repeating what every agent needs
- [x] The provider client, model settings, logging hooks and turn caps are configured once and apply to every agent
- [x] Any model failure, refusal or cap breach becomes work for a human, enforced in one place rather than per agent
- [x] Guardrails and handoffs have somewhere to live, whether or not any agent uses them yet — *a place, not an implementation: neither is invented ahead of a use*
- [x] Task parameter types are reachable without importing the triage agent
- [x] One scripted-model seam drives every agent's tests; none makes a network call
- [x] Triage behaves exactly as before, proven by its existing tests passing unchanged


## Delivered

`friday/harness.py` builds an agent from configuration and runs it. An agent now
declares only what makes it different — instructions, tools, what it does with
the answer.

The count is the argument. Before: nine references to the client, the model
wrapper, the run config and the tracing switch across two agents. After: nine in
the harness, **zero** in either agent.

`run()` returns `None` rather than raising. Each agent turns that into its own
kind of work — triage into a task for a human, the responder into a fall back to
the template — so neither catches anything. The reason is kept scrubbed in
`last_error`, because it is stored against a task and a provider exception can
quote an Authorization header.

`extra_turns` is the one thing that genuinely differs and could not be hidden:
triage's answer arrives as a tool call, which is the call *and* its result where
a written answer is one turn. Naming it beats a `+ 1` in one agent and not the
other.

## The wrong-direction dependency

`friday/workflows/` imported `ApiIssueParams` from `friday.triage` — reaching for
the schema of a task's parameters *through the agent that happens to fill them
in*. Those types describe the work, not the thing that recognised it, so they
live in `friday/models.py` now. It matters beyond tidiness: `plan_by_required_parameters`
reads their annotations to decide what a task cannot proceed without, and that
rule has nothing to do with triage.

## Written second on purpose

This is the ticket that says one agent is a hypothetical seam and two is a real
one. Building it against triage alone would have meant guessing at what varies;
with the responder in hand, what varies turned out to be instructions, tools,
tool-use behaviour, and a turn count — and nothing else.
