# 14: One harness for every agent

**What to build:** Adding an agent stops meaning copying the last one. An agent is
declared — instructions, tools, output shape — and something else knows how to run
it. Proven by the responder being the second agent to go through it, with triage
unchanged in behaviour.

**Blocked by:** 04. **Do not start before a second agent exists.** One agent is a
hypothetical seam; two is a real one, and building this against triage alone means
guessing at what varies.

**Status:** blocked

`friday/triage/__init__.py` currently declares the task parameter types, the tools,
the prompt, the model wiring, the error policy and the regex hygiene in one 310-line
module. The responder needs the model wiring, the hooks, the caps and the error
policy — and under the present shape it can only get them by copying them.

There is also a wrong-direction dependency: `friday/workflows/` imports
`ApiIssueParams` from `friday.triage`, reaching for the task's parameter schema
through the agent that happens to fill it in. Those types belong in `friday.models`.

- [ ] A new agent is added by declaring what makes it different, not by repeating what every agent needs
- [ ] The provider client, model settings, logging hooks and turn caps are configured once and apply to every agent
- [ ] Any model failure, refusal or cap breach becomes work for a human, enforced in one place rather than per agent
- [ ] Guardrails and handoffs have somewhere to live, whether or not any agent uses them yet
- [ ] Task parameter types are reachable without importing the triage agent
- [ ] One scripted-model seam drives every agent's tests; none makes a network call
- [ ] Triage behaves exactly as before, proven by its existing tests passing unchanged
