# 43: The responder's prompt is one module

**What to build:** One module that answers "what does the responder's prompt
look like" — today that answer is spread across four places: the persona file,
the prompt text file, the draft method's bundle construction, and the shared
bundle's slot order. After this, the persona and text files stay where they
are, and *everything assembled* is in the responder's own prompt module.

**Blocked by:** None (can start immediately)

**Status:** done

## What moves

- `build_instructions(persona, skill_guard)` — the stable half: the Responder
  persona section, the job text, the precedence sentence for the channel
  sections. Built once, cached by the provider.
- `build_input(room, counterpart, skills, tone, conversation, params, asking)`
  — the per-call half, as its **own ordered render**: the responder stops using
  the shared bundle and orders its own sections, stable-first.

## What must survive, by test

The shared bundle's one real property is that two calls differing only in the
conversation share a byte-identical prefix — that is the prompt-cache hit. The
responder's own render must keep it, and the existing prefix test moves here
and runs against this builder.

The stranger line, the room's three layers, the `people:` map, the task's
explicit nulls: all already tested; those tests keep passing untouched.

## Acceptance criteria

- [x] The draft path assembles nothing inline; it calls the prompt module
- [x] Sections render stable-first, and the byte-identical-prefix test passes
      against the responder's own render
- [x] The composing graph node keeps producing the same message input (it is
      the same family; whether it shares this module or keeps its thinner
      assembly is the implementer's call — but the *text* it uses may not fork)
- [x] Escaping still happens at the one seam
- [x] Output byte-identical to today's for the same inputs, captured not
      eyeballed

## What it came to

`friday/responder/prompt.py`: `build_instructions(persona)` and a keyword-only
`build_input(...)` rendering nine sections stable-first. The counterpart text
loads at import, so a missing file fails at startup rather than at the first
stranger. The prefix property has its own test against this builder. The
composing node keeps its thinner assembly — same text files, no fork. All ten
captured prompts byte-identical.
