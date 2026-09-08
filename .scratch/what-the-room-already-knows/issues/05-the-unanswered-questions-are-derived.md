# 05: The unanswered questions are derived, and the extractor sees them

**What to build:** The system knows which questions it has asked and not had
answered, and stops asking again. In the recorded flow it asked for an
environment, the reporter did not answer, and nothing in the system knew it was
waiting — so the next pass was free to ask again.

The cheapest real context on this board: it is a query over what was actually
sent, so it cannot be wrong in an interesting way, and it adds no model call.

**Blocked by:** 01

**Decisions:** D2, D10

**Status:** ready-for-agent

- [ ] A conversation's unanswered questions are derived from the outbound
      requests for details that have no later reply from the reporter
- [ ] Derived, not summarised: this ticket adds no model call, and a test says
      so
- [ ] They reach the extractor through the context the build assembles, in the
      same section machinery 01 established
- [ ] An extraction whose task has already asked for a field does not ask for it
      again in the same terms
- [ ] A question that has since been answered stops appearing
- [ ] A conversation that has asked nothing renders no such content
- [ ] The guard is deleted once and watched go red
