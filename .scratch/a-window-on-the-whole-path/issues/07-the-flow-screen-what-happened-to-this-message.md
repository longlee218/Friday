# 07: The flow screen — what happened to this message

**What to build:** One message, and everything that followed from it, as an
ordered path.

**Blocked by:** 02, 05

**Decisions:** D5, D6

**Status:** done

## Why

This is the operator's first request, redefined by what the data supports.
"Flow, step by step" cannot mean the graph — every graph has one node — so it
means the path through the process, and ticket 02 builds the endpoint that
assembles it.

The question this screen exists to answer is the one that has been unanswerable
without reading raw logs: **"why did it do that to this message?"** Every
ticket on the `discord-mention-triage` board that came out of watching real
threads is a variant of it — the reporter replied and nothing heard the answer,
the details came in a second message and nothing read it. Each was found by a
person staring at Discord and guessing. This is the screen where the guess
becomes a reading.

Two paths matter more than the happy one, and both are easy to leave out:

**The skip.** A message classified `skip` opens no task, so a task-spined view
would not show it at all — and "why did it ignore me" is the most common thing
an operator wants to interrogate. The spine is the message precisely so this
renders (D5).

**The hold.** The sensitive-word prefilter stops a message *before any model
call*, so there is no prompt to show and no confidence to display. Rendered
naively that looks like the pipeline lost the message. It must read as a step
that happened on purpose, naming the word that caused it.

## Acceptance criteria

- [x] A path renders as ordered steps: arrived → (held?) → turn closed →
      classified → task opened or skipped → extraction → question or hand-over
      → queued → approved → sent or failed
- [x] A step that did not happen is *absent*, not shown as empty — but a step
      that happened and produced nothing (a search that found nothing) is
      present and says so
- [x] The skip path renders as a complete, correct outcome
- [x] The held-by-prefilter path names the word that held it and makes clear no
      model saw the message
- [x] Each step carries its own evidence: the prompt behind the classification,
      the confidence, the tokens, the latency
- [x] The classification shows its confidence **against the configured
      threshold** — and it did not until a review checked: the page hardcoded
      `0.70`, which agreed by luck and would have diverged silently the first
      time the operator tuned it. `/api/board` serves the real value now, and
      the composition root asks `TriageRunner` for it rather than reading
      `config.yaml` — which an architecture test caught me doing, since "0.62" means nothing without knowing the line is at
      0.7 — that number is `confidence_threshold` in `config.yaml`, documented
      there as a placeholder nobody should trust yet, and this screen is how it
      would ever stop being one
- [x] Getting to a flow is easy from the message feed and from a task
- [x] Built through the `ui-ux-pro-max` skill; a vertical timeline is a chart —
      colour alone must not carry the difference between a step that succeeded
      and one that failed

## Notes

The screen will be empty until the agent is restarted and new messages arrive.
The database was wiped in ticket 01, and the pre-migration rows that existed
before it had no `task_id`, `node` or tool calls anyway. Build against a
`poke.py`-style hand-fed message or a throwaway database rather than waiting.

`poke.py` is currently deleted in the working tree and untracked-for-deletion —
that is unrelated to this board and unresolved. If it is restored it is the
fastest way to produce a real path end to end without a second Discord account.

Do not add a graph visualisation (out of scope). If a multi-node graph ever
exists, this screen is where its nodes would nest, as one step containing
several — which is an argument for making a step a container from the start,
not for building the container's contents now.
