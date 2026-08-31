# 08: Record observations during a task

**What to build:** A reasoning step can note something it learned while working, so the
observation is not lost when the step ends. Nothing it writes influences anything yet.

**Blocked by:** 04

**Status:** done

- [x] A step can record an observation supplying only a category and text
- [x] The recorded entry carries the originating task and a timestamp, attached by the runtime rather than supplied by the model
- [x] An observation in an unrecognised category is rejected
- [x] Recorded observations are scoped to their task and do not appear in any prompt
- [x] Observations survive a restart


## Delivered

`friday/observations.py` builds the `remember` a step calls, bound to one task —
so a step cannot record against another by naming it. The parameters a model
supplies are the parameters it can get wrong, which is also why the task and the
timestamp are attached by the runtime: a model asked for a time invents one.

An unknown category comes back as **a result the model can act on**, not an
exception. A raising tool ends the run, and the step calling it had more to do
than write a note.

The categories are closed — `fact`, `person`, `lesson` — because an open
vocabulary is one nothing can later query.

## The criterion that is the whole ticket

> Recorded observations are scoped to their task and do not appear in any prompt

There is a test that greps the source for readers of `observations()` and fails
if anything outside `db.py` has one. It looks like a strange thing to assert
until you notice that the failure it prevents is silent: an agent fed its own
unreviewed notes drifts, and nothing about the output says so.

`promoted_at` exists and nothing sets it. Ticket 10 does, and only for work an
approval corroborated — naming the column now means the shape is complete rather
than migrated in later.
