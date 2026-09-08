# 08: The build respects a budget

**What to build:** A build stops growing without limit — by budget, not by
message count, because a count cannot tell twenty short messages from twenty
long ones. This is the ticket where the three compaction paths meet: a field the
schema names goes to the task's parameters, verbatim material goes to an
artifact, and only prose reaches a model.

**Blocked by:** 04, 06, 07

**Decisions:** D5, D6, D7, D8

**Status:** ready-for-agent

- [ ] Budget is configured per phase in estimated tokens, and the configuration
      says it is an estimate — there is no tokenizer for the provider this
      system calls
- [ ] Message count remains a secondary upper bound, never the primary trigger
- [ ] An unset budget means no compaction at all
- [ ] A budget clause that cannot be evaluated fails at startup and is never
      silently dropped — the failure shape that emptied five mechanisms here
- [ ] Compaction splits three ways, and a model only ever summarises prose
- [ ] A schema field already extracted is not re-read from the transcript
- [ ] Two compactions that did not bring the build under budget stop compaction
      being attempted for that conversation, with a cooldown, and the condition
      is visible to the operator rather than a repeating cost
- [ ] No rolling per-exchange summarisation: it rewrites the prompt prefix every
      pass
- [ ] A test drives two ineffective compactions and asserts the third is not
      attempted
- [ ] Guards deleted once and watched go red
