# 04: Node 0 stops paying twice for the same extraction

**What to build:** A task waiting for details in a busy room stops re-billing an
identical extraction on every pass. One task in the recorded data has two
extractor calls of 1,790 input tokens whose prompts are byte-identical — node 0
is excluded from the checkpoint and re-executes every pass, correctly, because
it must see messages that arrived since the last one. What it must not do is
call a model when nothing arrived.

Independent, and early on purpose: it is a pure cost fix, verifiable against
the flow already recorded, and it stops money leaking while the rest is built.

**Blocked by:** None (can start immediately)

**Decisions:** D23

**Status:** ready-for-agent

- [ ] Node 0 compares before calling: the task's parameters and the reporter's
      messages both count toward "has anything changed"
- [ ] Nothing changed — no model call, and the previous result stands
- [ ] Something changed — extraction runs, exactly as today
- [ ] Node 0 still sees a message that arrived since the last pass, which is the
      reason it is excluded from the checkpoint in the first place
- [ ] A test drives two passes with nothing new between them and asserts one
      model call, not two
- [ ] The guard is deleted once and watched go red
