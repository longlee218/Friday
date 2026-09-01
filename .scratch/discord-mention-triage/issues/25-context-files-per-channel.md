# 25: What the agent knows about this channel

**What to build:** The operator can tell the agent things it cannot learn — that this
channel is project A, that its logs live in namespace B — and the agent can add what
it has learned, without either overwriting the other.

**Blocked by:** None (can start immediately)

**Status:** done

Two kinds of knowledge share one file, and the split has to be visible in it. The
**derived** part is written by the machine from what it has learned and is safe to
delete: it rebuilds. The **overrides** part is the operator's, and nothing may write
to it — that is what makes a correction stick, rather than surviving until the next
rebuild ten seconds later.

A file per channel, inheriting from a base file that holds what is true everywhere —
who the agent is, and how it behaves. YAML rather than JSON, for comments: a fact
without its reason is a fact nobody dares change.

Rebuilding is triggered by learning something, not by a clock. The promotion pass
already knows whether anything changed and already runs on a timer, so a rebuild
happens when there is something to rebuild and not otherwise.

The derived part carries what has been learned, and a summary of the conversation —
but the summary costs a model call, so it is written only when the conversation has
grown past a configured share of the model's context window. Below that, the raw
messages are cheaper than summarising them.

- [x] The operator can create a channel's file and fill it in before the agent has learned anything
- [x] What the operator writes is never overwritten by a rebuild, and the file says plainly which part is theirs
- [x] Deleting the machine-written part loses nothing that cannot be rebuilt
- [x] Values in the base file apply everywhere; a channel's file overrides them
- [x] A rebuild happens when something has been learned, not on a fixed schedule
- [x] A conversation summary is written only once the conversation is large enough to be worth the call
- [x] A file that cannot be parsed is reported at startup, naming the file, and does not stop the agent running without it

## Done

`friday/channel_context.py` holds `ContextStore` (one YAML file per channel,
inheriting `base.yaml`; `derived` and `overrides` sections, each file
carrying a comment explaining which is which) and `ContextRebuilder`
(renders `Promotion`'s notes and, past a configured share of the model's
context window, a conversation summary — via `friday.harness.Harness`, same
as every other agent since ticket 23).

The rebuild trigger rides `Heartbeat`'s existing cadence rather than owning a
timer: `Heartbeat.run_forever` calls a new `promote()` method, which runs one
promotion pass and rebuilds every known channel's derived section only if
that pass actually promoted something. `init_channel.py` is the operator's
entry point — creates an empty `overrides` section for hand-editing, and
refuses to overwrite a file that already exists.

`AgentConfig` gained `context_window` (no sane default exists across
providers, so it's configured per agent) and `Config` gained a `context`
section (`directory`, `summary_share`). `validate_all()` is called once at
startup in `run_agent.py` and logs a warning per unparseable file by name;
nothing about a bad file stops the process — that channel simply runs
without it.

Not built: which channels get a file is scoped to files that already exist
(`known_channels()`), not auto-discovered from message history — the
config's `watched_channels` whitelist would be the natural source if that
needs to widen later. Token counting for the summary threshold is `len(text)
// 4`, not a real tokenizer — precise enough for a configured *share* of a
window, not for billing.

9 new tests in `tests/test_channel_context.py`, including the trigger
condition, tested via `Heartbeat.promote()` directly rather than the
infinite `run_forever` loop (same reason `test_liveness.py` already tests
`beat()` rather than the loop). Full suite: 324 passed, 315 before this
ticket, none of the existing ones modified.

Deliberately not touched, per instruction — ticket 26's territory: `reply_to`
on inbound messages, the structural context-relevance filter, and the
anchored-prefix conversation shape. `ContextRebuilder._maybe_summarize` reads
the conversation's messages unfiltered (`db.messages`); once ticket 26 lands
a filtered/anchored seam, swapping that one call is the whole integration —
nothing here assumes today's unfiltered shape.
