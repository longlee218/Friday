# 22: Answer from a trace

**What to build:** A traced report becomes a written answer in the operator's voice,
shown to them for approval, and posted as them when they approve it. This is the
whole loop closing: someone reports a broken API, and gets a real answer.

**Blocked by:** 21

**Status:** ready-for-agent

This is the first thing that produces a `reply`. Everything downstream of one is
already built and has never run: the approval card, the join that will not release
an unapproved reply, the staleness check that refuses an answer the conversation has
moved past. This ticket is what gives all of it a producer.

It is also the first time the agent says something that can be **wrong** rather than
merely unhelpful. Asking for a correlationId costs a question; asserting a cause
costs the operator's credibility with their own team. That asymmetry is why the
approval gate exists, and why it stays for this even though it was dropped for
asking.

An answer that the logs do not support is worse than no answer. The model has to be
able to say it does not know, and that has to route to a human rather than being
dressed up as a finding.

- [ ] A task with a trace produces a drafted answer in the operator's voice
- [ ] The draft says what the logs showed, and does not assert anything they do not
- [ ] A trace the model cannot explain produces no draft, and the task goes to a human
- [ ] Nothing is posted until the operator approves it
- [ ] An answer the conversation has moved past is not posted, even after approval
- [ ] The reply lands in the conversation it answers, as the operator
- [ ] The task is finished once the answer is out, rather than staying open
