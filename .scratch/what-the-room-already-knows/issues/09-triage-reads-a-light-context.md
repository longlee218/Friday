# 09: Triage reads a light context

**What to build:** Triage is shown this person's turn and a short summary of the
room, instead of every relevant message the conversation has ever held. Today it
receives an unbounded window — 24 lines over seven days in the recorded flow,
growing forever — to decide one label and one number.

This reverses ticket 26, which chose the unbounded window deliberately for
prompt-prefix stability and has three tests asserting it. What that bought is
real; what it cost is 2,552 characters of transcript in the highest-volume
prompt and a hallucinated colleague. The tests move up a layer rather than out.

**Blocked by:** 02, 03, 06

**Decisions:** D2, D3, D24

**Status:** ready-for-agent

- [ ] Triage's input is the turn plus the room's summary, under the light budget
- [ ] Domain memory, task parameters and artifacts do not reach triage — it
      decides a label, not a value
- [ ] The three tests that pinned the unbounded window are rewritten one layer
      up: a summary stable between calls when the room has said nothing new, so
      prefix stability is still asserted where it is now true
- [ ] The reversal of ticket 26 is stated in this ticket and corrected in the
      file that records what exists, in the same commit
- [ ] A reporter's reply to something this system asked still reaches the task
      that asked, by the id lookup that already does it and not by prompt
      context
- [ ] Triage's tool schema is unchanged: no project, no topics, no entities
- [ ] The evaluation is run against the frozen set, and accuracy, the confusion
      matrix and the threshold table are reported alongside the change
- [ ] Guards deleted once and watched go red
