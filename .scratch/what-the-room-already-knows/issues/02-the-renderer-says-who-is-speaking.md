# 02: The renderer says who is speaking, and says it once

**What to build:** Every rendered line of conversation says whether the watched
account or somebody else wrote it, and a message that reaches a prompt by two
paths appears once.

Prefactor. It makes 09 smaller and it fixes the observed harm directly: 23 lines
carried the identical author name because the room is a self-test channel, and
the model invented a colleague whose name appears only inside the body of one
message. The duplicate is separate and mechanical — the mention enters through
the relevance window on its mention clause, then the turn is appended carrying
the same message id, and nothing deduplicates.

**Blocked by:** None (can start immediately)

**Decisions:** D2

**Status:** ready-for-agent

- [ ] Each rendered line distinguishes the watched account from everybody else
- [ ] The marker is placed outside the escaped span, where the timestamp already
      is, so nothing is escaped twice
- [ ] A message that both the relevance window and the appended turn carry is
      rendered exactly once, matched on its message id
- [ ] A turn of three messages contributes three lines to the prompt, not six
- [ ] The existing test that forbids double-escaping a quoted section stays
      green
- [ ] Both guards deleted once and watched go red
