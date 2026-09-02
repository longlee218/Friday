# 42: The single-call families own their prompts

**What to build:** Triage and the extractor each get one module that answers
"what does this family's prompt look like, and from what is it assembled" —
`build_instructions(...)` for the stable half, `build_input(...)` for the
per-call half. Opening that one module shows the whole prompt; nothing else
assembles any part of it.

**Blocked by:** None (can start immediately)

**Status:** ready-for-agent

## Why these two together

They are the two trivial families — triage's input is the turn's messages and
nothing else, the extractor's is a schema and what the reporter said — and each
is one short move. Separately they are half-hour tickets; together they are the
warm-up that proves the shape before the responder, which is the family with
real assembly.

## The shape being proved

- The *text* stays in `prompts/*.md` — that split landed yesterday and is not
  being reversed. The new module loads it and assembles around it.
- Triage's examples block (the operator's ✅-marked classifications) is part of
  `build_instructions`, because it belongs to the stable, cached front.
- The extractor's schema lines (each field's `doc` metadata) are part of
  `build_input`, built from the params class as they are today.
- Escaping stays where it is. The builders call the one seam; a second
  escaping site is the bug that already happened once.

## Acceptance criteria

- [ ] One module per family owns every assembled byte of that family's prompt;
      the family's runtime code calls `build_*` and concatenates nothing itself
- [ ] The assembled output is byte-identical to today's — proven by capturing
      both before and after, not by eyeballing
- [ ] `test_no_family_text_appears_in_another_familys_prompt` still passes
- [ ] No new escaping site; the one-seam grep test still passes
