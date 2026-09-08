# 01: A room fact the extractor uses

**What to build:** The operator can write down what something in a room actually
*is* — `test.apero` is staging — and the next task in that room fills that field
from it instead of asking the reporter. Demoable end to end: open the room's
context on the board, add the fact, and the extraction that previously sent
"URL `test.apero` a chưa biết là env nào em?" fills `environment` and sends
nothing.

This is the board's tracer bullet. It cuts the narrowest complete path through
producer, store, section and consumer, and it is producer ① — the one D19 says
this board may not ship without.

**Blocked by:** None (can start immediately)

**Decisions:** D2, D4, D12, D13, D15, D19, D21, D22

**Status:** ready-for-agent

- [ ] The operator can read and write a room's own facts through the board, and
      a value written by hand survives the next summary rebuild
- [ ] Extraction reads the room's context — all three layers, in the precedence
      order they already have — where before only the responder did
- [ ] The room's facts reach the extractor through the existing memory section
      builder's channel slot, which gains its first caller since it was written
- [ ] The section is rendered into the extractor's **per-call input**, never its
      instructions (D21)
- [ ] The section is length-framed, so stored text cannot forge its own
      boundary (D12)
- [ ] A room with no context file renders no section at all, and in that case
      the extractor's prompt is byte-identical to today's
- [ ] With a fact naming what `test.apero` is, an extraction that previously
      asked for `environment` fills it and queues no question
- [ ] The context package returns content; the extraction family renders it. The
      package constructs no section, imports no section builder, joins nothing
- [ ] The two `ast` guards on prompt assembly stay green without being relaxed
- [ ] The frame guard is deleted once and watched go red
