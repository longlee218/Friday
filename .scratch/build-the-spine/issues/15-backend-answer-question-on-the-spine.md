Status: ready-for-agent
Blocked by: 14

# `backend.answer_question` on the spine

Decision: [Designing `backend.answer_question`](../../domains-plug-in/issues/06-designing-answer-question.md) (as amended by 17).

## Goal

- `backend.project` gains `purpose` (Alembic); the channel's projects reach
  the toolset factory through `RunContext`.
- `plugins/backend/agents/explain.py`: `backend.explain`, strong tier,
  `max_turns 20`, result `Explanation(verdict, answer, refs, conclusive,
  next_checks)`; code fills repos searched and ref read.
- `plugins/backend/actions/answer_question/`: contract `backend.code` +
  `backend.docs`, `max_replans 1`, `max_steps 3`, Reply waits for approval;
  its own `acknowledge` text (or none).
- Acceptance check: `no` + `conclusive` needs a ref to where it would be
  done; an unknown ref voids the answer.
- The old doc-question graph goes.

## Acceptance

- [ ] The three real questions in 06 each produce a grounded `Explanation`
      on synthetic fixtures.
- [ ] `no` + conclusive with no "where it would be" ref is refused (test).
- [ ] A `repo` outside the channel's projects is refused (test).
- [ ] Whole suite green; `code-review` done.
