---
name: implement
description: "Implement a piece of work based on a spec or set of tickets."
disable-model-invocation: true
---

Implement the work described by the user in the spec or tickets.

Use /tdd where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, use /code-review to review the work.

Close out the ticket. This tracker has no "done" label; a finished ticket is
one whose checklist is satisfied and whose work is committed (see
`docs/agents/issue-tracker.md`). So before you report completion, for each
ticket you finished:

- Tick every acceptance box it lists — `- [ ]` → `- [x]` in
  `.scratch/<feature-slug>/issues/<NN>-*.md` — but only for items you actually
  verified. Leave a box unticked (and say so) if its criterion is unmet.
- Set the ticket's `Status:` line to `done` (the completion state — see
  `docs/agents/triage-labels.md`). Use no other completion string.
- Do not touch `.scratch/progress.jsonl` here — the ticket file is where a
  ticket's status lives.

Commit your work to the current branch, including the ticked ticket file, so
the completed checklist lands with the change it describes.

In your final report, state which boxes you ticked and name any you left
unticked and why.
