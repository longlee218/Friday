# Effective Harnesses for Long-running Agents — Anthropic

Two companion posts on Anthropic's Engineering blog, both primary-sourced (fetched
and read directly, not summarized from secondary write-ups):

1. **"Effective harnesses for long-running agents"** — Justin Young, 26 Nov 2025.
   <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents>
   Covers a single-agent (initializer + coding agent) harness for multi-session
   coding work.
2. **"Harness design for long-running application development"** (companion/
   follow-up piece surfaced by the same search, at a sibling URL).
   <https://anthropic.com/engineering/harness-design-long-running-apps>
   Covers the later three-agent (planner/generator/evaluator) harness and states
   the general design philosophy explicitly.

Neither post is titled exactly "durable execution" or "session continuation" —
those are my search terms, not Anthropic's. Both are the closest and most
directly on-topic first-party material found; no closer single document exists.
I did not find separate first-party material specifically about timeout/retry
policy or token-budget enforcement as a topic in its own right — see the gaps
noted below, which is itself relevant to Friday.

## Key concepts

**Discrete sessions, no memory between them.** The core problem statement in
post 1: agents "must work in discrete sessions with no memory of prior work,"
compared to "engineers working in shifts, where each new engineer arrives with
no memory of what happened on the previous shift." Two named failure modes:
agents attempting whole applications in one pass (half-implemented features when
context runs out), and agents prematurely declaring success after seeing partial
progress.

**Handoff via structured artifacts, not memory.** The fix is not a smarter
context strategy inside one session — it's externalizing state so the *next*
session's cold start is short: an `init.sh` to restore the environment, a
`claude-progress.txt` progress log, a granular JSON feature list the agent may
only flip a `passes` field on ("It is unacceptable to remove or edit tests"),
and git commits with descriptive messages as the actual checkpoints. Each new
session is told to "read the git logs and progress files to get up to speed"
before doing anything.

**Context resets over compaction, provisionally.** Post 2's harness used full
context resets between "sprints" rather than in-conversation compaction: "while
compaction preserves continuity, it doesn't give the agent a clean slate, which
means context anxiety can still persist. A reset provides a clean slate."
Notably, this was *walked back* as models improved — with a later model
generation the team "moved to a more methodical approach" and removed sprint
decomposition entirely, relying on the SDK's own automatic compaction. The
harness mechanism was explicitly provisional, tied to a model capability
level, not a fixed architectural truth.

**Separating the doer from the judge.** Post 2's planner/generator/evaluator
split exists because "when asked to evaluate work they've produced, agents tend
to respond by confidently praising the work — even when, to a human observer,
the quality is obviously mediocre." Separating the evaluator agent is "a strong
lever," but not free: "tuning a standalone evaluator to be skeptical turns out
to be far more tractable than making a generator critical of its own work" —
i.e., the skepticism has to be engineered into the evaluator's own prompt, it
doesn't fall out of separation alone.

**The governing principle.** Post 2 states the design philosophy directly:
"Every component in a harness encodes an assumption about what the model can't
do on its own, and those assumptions are worth stress testing, both because
they may be incorrect, and because they can quickly go stale as models
improve." Corollary: "find the simplest solution possible, and only increase
complexity when needed." This is presented as the reason sprint decomposition
was later removed — the assumption behind it (models need externally-imposed
task chunking) went stale.

## Related primary sources found

- <https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents> (post 1, initializer/coding-agent harness)
- <https://anthropic.com/engineering/harness-design-long-running-apps> (post 2, planner/generator/evaluator harness, general philosophy)
- <https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk> (adjacent — Agent SDK primitives, surfaced in the same search, not fetched for this doc)
- <https://anthropic.com/engineering/managed-agents> (adjacent — server-hosted agent runtime, surfaced in the same search, not fetched for this doc)

## Relevance to Friday

**What the source validates directly:** the handoff-artifact pattern is the
same shape as Friday's DAG checkpointing. `friday/sdk/workflow.py` checkpoints
after every node and discards state when task parameters change — this is
exactly "a structured handoff that carries the previous agent's state and the
next steps," just automated (a DB row) instead of a file an agent writes by
hand for its own future session to read. Friday's version is stronger in one
respect the posts don't need: it has an explicit invalidation rule (params
changed → state is stale), where Anthropic's harness relies on the agent
choosing to trust or re-derive the progress file it reads.

**What the source does not address, and this is itself informative:** neither
post discusses per-attempt timeout budgets, retry whitelists, or the
provider-client's-own-retry-vs-recorded-retry mismatch that motivated Friday's
`max_retries=0` + `Harness._attempts` design. This isn't a gap in my search —
both posts are about *session-to-session* continuity for hours/days-long coding
runs, not about bounding a single model call. Friday's "one seam, one clock"
design (`_settle`) is a different and complementary layer: it's the mechanism
that makes any *one* node in a DAG-like graph well-behaved, whereas Anthropic's
posts are about what happens *between* nodes (sessions) when a whole run
outlives one context window. Friday doesn't currently have Anthropic's
session-boundary problem at all — its DAG runs complete within one process
lifetime; the checkpointing exists for *task*-level durability across restarts,
not for context-window exhaustion. So this source neither validates nor
contradicts the per-attempt timeout-sharing design; it's silent on that layer
entirely, and no other first-party Anthropic material I found treats it as a
named topic either (it's absent from both fetched posts under "Notable Gaps").

**Fail-open on budget-check read failure:** not discussed in either post —
out of scope for both (neither harness meters per-agent spend as a gate).

**Keeping a tested, callerless checkpoint/resume mechanism:** post 2's governing
principle — "every component encodes an assumption... worth stress testing...
because they can quickly go stale" — cuts both ways here. It argues *for*
keeping Friday's checkpoint/resume machinery only if there's a concrete belief
it'll be needed by a future multi-node graph (the posts' own sprint-decomposition
mechanism was removed once its assumption went stale, not kept "just in case").
Read strictly, the principle is a mild argument to either delete the unused
mechanism now or name the specific future graph that will need it — "tested,
no current caller" is precisely the kind of complexity the source says to
justify by removing, not by leaving in place on the theory it might be useful.

**Separation of doer and judge maps onto Friday's own operator-approval gate**:
Friday's outbox already enforces this — nothing composes and sends in one step
without the approval predicate in between, and no agent grades its own reply.
The posts' finding that agents over-praise their own work is a specific,
useful citation for *why* that gate matters, beyond "someone should review it."
