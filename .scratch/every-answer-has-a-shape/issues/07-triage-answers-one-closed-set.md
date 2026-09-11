# 07: Triage answers one closed set, `skip` included

**What to build:** triage returns one validated decision — a member of a closed
set generated from the registry of task types plus `skip` — and a confidence
(D6). This **reverses** the recorded split between `classify` and `skip`, and the
reversal is the point: two tools meant two validations, and an invented type could
reach the runner and open a task the pool then discovers has no graph. One enum,
validated once, before anything is opened. What the old split protected — that
`skip` opens nothing — is the runner's business and stays there.

The evaluation runner follows the new shape, and owes one new number: a decision
outside the closed set is counted and reported separately, because "it invented a
type" and "it chose the wrong type" are two different failures and counting them
together would hide the one this change exists to make impossible (D20).

**Blocked by:** 03, 05.

**Status:** ready-for-agent

- [ ] A decision outside the closed set never opens a task.
- [ ] `skip` is validated exactly as strictly as every other member, and still opens nothing.
- [ ] Triage's answer is the return value of the call that asked for it; nothing is read off a capture.
- [ ] The eval runner scores the new shape and reports out-of-set decisions as their own number.
- [ ] The classifier is scored against the frozen set and compared to ticket 03's baseline, with accuracy, confusion matrix and threshold table reported.
- [ ] CLAUDE.md's record of the old split is corrected in the same commit.
