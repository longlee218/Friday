# 05: The unreviewed ask is checked before it speaks

**What to build:** A predicate over the responder's reworded ask. It passes, it
goes out; it fails, the template goes out.

**Blocked by:** None (can start immediately)

**Decisions:** D8

**Status:** todo

## Why

`auto_ask_for_details: true` is the one message allowed out with no approval,
and `config.yaml` justifies it in one sentence: *"what is being asked never
changes, only the wording does, and the responder falls back to the plain
template if it fails to write one."*

The first half is not enforced anywhere. `Pool._in_the_operators_voice`
(`friday/tasks/pool.py`) calls `responder.draft(...)` and returns `draft.text`
unchanged. The fallback covers `None` — the model failing — and nothing covers
the model succeeding at writing something else.

The responder's input includes `await self._db.relevant_messages(...)`: text
other people wrote in the channel. The trust boundary work (tickets 06, 07 on
the other board) makes that text *inert as prompt structure*; it does nothing
about the text the model then writes. So the one path with no human in it is
also the path whose wording is model-authored from untrusted input, sent under
the operator's name.

This has already happened once in a milder form, and the incident is recorded
in `Responder.draft`'s own docstring: asked to request a correlationId, the
model found one belonging to a different report and wrote "ok có correlationId
rồi, để anh trace thử" — a promise nobody would keep.

The rule this follows is the one `friday/dag/prepare.py` already states for
validation: code is the floor, and a model's contribution is accepted only
where code has nothing to object to.

## Acceptance criteria

- [ ] A predicate the ask must pass: still names every field the template asks
      for, no URLs, no code blocks, under a length bound, no commitment verbs
- [ ] A draft that fails it is discarded and the template is sent — logged at
      info with the reason, since a silent fallback hides a prompt regression
- [ ] Driven at `Pool.run_once` with a responder stub returning hostile text,
      asserting the queued row holds the template
- [ ] The sentence in `config.yaml` is rewritten to say what enforces it
- [ ] Each guard is deleted once and watched go red
