# 10: Promote observations to long-term notes

**What to build:** Observations recorded during work become durable notes the system
reuses — but only the ones whose task the human approved, so a wrong guess made under
pressure does not become a permanent belief.

**Blocked by:** 06, 08

**Status:** done

- [x] Observations from an approved task are promoted into the long-term notes
- [x] Observations from a task that was never approved are not promoted
- [x] Promotion rules differ by category, so categories needing corroboration are treated differently from those that do not
- [x] Promoted entries track how many approved tasks support them
- [x] Long-term notes stay within the configured size limit
- [x] The notes file is rewritten rather than appended to, so it stays stable between promotions — *in the database, not a file; the property wanted from one is kept, see below*
- [x] Long-term notes appear in the stable, early portion of each prompt
- [x] Staged observations are cleared once promotion has considered them


## Delivered

An observation is a guess made while working; a note is something the system
acts on for months. Approval is what separates them — a wrong guess made under
pressure must not become a permanent belief.

Approval alone is not enough for every category. One reading of how a system
works can be wrong and a habit seen once is not a habit, so `fact` and `person`
need two approved tasks to agree. A `lesson` needs one: the operator already
made that judgement by approving the work.

**Considering an observation uses it up**, promoted or not. Leaving it staged
means the next pass counts the same evidence again, and one observation
corroborates itself into a belief.

Because they are used up, a first sighting has nowhere to wait — so a note row
exists **below** its threshold, holding the support so far. Evidence and belief
are the same table; the thresholds decide which is which, and they live in
`friday/notes.py` rather than in a query.

A trim keeps the **best supported**, not the newest: something five approved
tasks agreed on outranks something seen once.

## Not a file, and why that is the same thing

The criterion says notes are a file rewritten rather than appended to. They are
rows. What the wording was protecting is real and is kept: the block is rebuilt
from the promoted set in a stable order — best supported, then alphabetical —
so two renders between promotions are byte-identical.

That is what lets it sit in the early part of a prompt. A byte that moves there
costs a cache hit on everything after it. Where the bytes are stored is not what
gives them that property, and putting them in a file would have been the one
thing outside SQLite.

It reaches an agent through its **instructions**, read once at build time, so a
promotion takes effect on the next start rather than invalidating a warm cache
mid-run.

## Empty until a planner writes to it

Nothing calls `remember` yet, so promotion runs over an empty table and
`render()` returns "". That is the honest state: the pipe is built and the water
starts flowing when the first agentic planner notes something.
