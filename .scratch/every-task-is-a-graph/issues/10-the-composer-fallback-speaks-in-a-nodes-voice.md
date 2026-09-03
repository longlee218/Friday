# 10: The composer's fallback speaks in a node's voice, and posts a raw diff

**What to build:** When `compose_reply` has no agent configured, the graph
stops handing the reporter a Node-family sentence with an unreviewed unified
diff stapled to it. Either it hands over, or the Responder writes it — but a
diff never reaches a reporter as prose nobody in the Responder family wrote.

**Blocked by:** None (can start immediately)

**Decisions:** the spec's Invariant, D14

**Status:** done

## Why

The invariant this board's last ticket added a test for is **only
Responder-family agents produce text that reaches a reporter**. It is broken
today, in the one configuration the docs call the normal degraded mode.

`friday/dag/api_issue.py`'s `_compose_reply` ends its `if cause:` branch with
`return Reply(said)`, reached whenever `deps.extra["compose_reply"]` is
absent — which is exactly what a fresh install and any deploy without a
`dag_compose` block has. `said` is:

    f"{cause}\n\n{fix}"

`cause` is `analyze_stack`'s own prose. `fix` is `fix_bug`'s **raw unified
diff**. That `Reply` is queued as `Kind.REPLY` under `discord_user`, so it
goes out in the operator's name, to the person who reported the bug, with a
patch in it. Two things are wrong at once and they are worth separating:

- **The voice.** Node-family text reaching a reporter is the precise thing
  the invariant forbids. Nothing in the Responder family touched this
  sentence; the register, the language, the stranger rule, the channel
  context — none of it applied.
- **The diff.** Ticket 07 built a second gate so a patch waits for the
  operator. This path routes the same patch to an *external reporter* with no
  gate at all — it is not applied anywhere, but proposing a code change to
  whoever filed the ticket is not what the outbox approval was protecting.

The invariant test does not catch it because it asserts *persona wiring* —
which node is built with which `Family` — and this path builds no agent at
all. A test that only checks how agents are configured cannot see text that
never went through an agent.

**How it was found:** a code review of the whole board (Opus, spec axis).
Three earlier review passes on the same diff reported "clean, no findings" —
this path is only visible if you read `_compose_reply`'s fallback rather than
the configured happy path.

**Worth noting:** `tests/test_dag.py`'s
`test_approving_resumes_the_exact_call_in_a_fresh_process` asserts
`"--- a" in drafted.text` — the bug is currently pinned as expected
behaviour by this board's own test. That assertion is part of what changes.

## Acceptance criteria

- [x] With no `compose_reply` agent configured, a graph that found a cause and
      a fix does not queue a `reply` containing the diff
- [x] Whatever it does instead is stated as a decision, not left implicit:
      hand over to the operator (the reason carries the cause and the fix), or
      send a Responder-written sentence that mentions neither verbatim
- [x] The invariant test is extended to catch text that reaches a reporter
      without passing through a Responder-family agent at all — not just
      mis-wired persona families
- [x] The extended test is deleted once and watched go red against the current
      code, so it is known to catch this
- [x] `tests/test_dag.py`'s `"--- a" in drafted.text` assertion is corrected
      rather than deleted, and its replacement says what the new behaviour is
- [x] CONTEXT.md's Hand-over / Graph entries say what an unconfigured composer
      does, since "it reproduces the deterministic planner it replaced" is no
      longer the whole story

## What it came to

`_compose_reply`'s `return Reply(said)` became
`return HandOver(f"Found something, but nobody wrote a reply: {said}")`. One
line, and it moves the same two pieces of material — the analysis's cause and
`fix_bug`'s diff — from the reporter to the operator, who is who they were
always for. Nothing else about the node changed: an agent that calls `answer`
still produces a `Reply` exactly as before, so the configured path is
untouched.

The fallback is reached from two directions and both are now covered: no
`compose_reply` agent at all (a fresh install, any deploy without a
`dag_compose` block), and an agent that ran but called no tool. The second was
already tested — `test_prose_with_no_tool_call_falls_back_to_the_raw_cause`,
now `..._hands_over_rather_than_being_read` — and it gained an assertion it
should have had from the start: the model's own wandering prose appears
nowhere in what the operator is shown.

Two new tests in `tests/test_dag_api_issue.py` pin the behaviour directly:
one that an agentless composer hands over rather than replying in a node's
voice, one that a diff never reaches a reporter. Both were watched fail
against the old code before the fix, and all three (with the resume test)
were watched fail again afterwards by restoring `Reply(said)`.

The invariant now has two halves and neither is sufficient alone, which is
written into the hygiene test's own docstring so the next reader finds both:
`test_only_responder_family_agents_can_speak_for_the_operator` checks *which
agent is built with which persona family*, and could not have caught this —
there was no mis-wired family, there was no agent. The behavioural half is
the two tests above.

`tests/test_dag.py`'s `test_approving_resumes_the_exact_call_in_a_fresh_process`
asserted `"--- a" in drafted.text` — this board's own test pinning the bug as
expected behaviour. It now reads the hand-over's stored question instead,
which is a stronger proof of the same thing: the cause from before the pause
*and* the approved diff both appear in it, and neither could unless the run
picked up at `fix_bug` and walked on to `compose_reply`. It also asserts no
reply row exists at all.

Two other tests asserted `isinstance(state["compose_reply"], Reply)` while
being about something else entirely (that the state accumulates, that the
edge to `fix_bug` fires); they assert `HandOver` now, with a line saying why.

633 tests pass (631 before, +2).
