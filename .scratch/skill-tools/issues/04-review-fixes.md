# 04: What the review changed

**What to build:** Nothing new — this records where the shipped code stopped
matching the spec, so a later reader trusts the tickets over the decisions
they were derived from.

**Blocked by:** 01, 02, 03

**Status:** done

Five of these passed a green suite of 651 tests, which is the only reason
they are worth writing down.

- [x] **D5 is wrong as written.** It said the escape belongs in
  `SkillLibrary`'s reader. It went to `metadata_for` instead, which put a
  format *and* its escaping inside a store and cost a third entry on the
  escape-seam allowlist. The renderer is `instruction_prompt.skill_metadata`
  now; the library hands over values. The allowlist is back to two, and an
  entry on it reads as "something is in the wrong module".
- [x] **D6's `location` was not absolute.** `config.py` defaults
  `skills_directory` to the relative `skills`, so production rendered
  `skills/x/SKILL.md`. The test was *named* for absoluteness and asserted
  only that the string ended in `SKILL.md`, so it passed on a `tmp_path`
  library whether or not anything resolved. It builds from a relative
  directory now, the way production does.
- [x] **D8's fourth rank was not token matching.** It split the corpus, which
  can only ever match an infix of the name — a query inside a description's
  word is already inside the description and caught a rank above. It splits
  the query now and needs every token; both halves were checked by mutation.
- [x] **D10's empty query returned `""`.** An empty string is the one answer
  a model cannot act on, against this module's own rule that a bad guess
  comes back as something it can. It answers with what is available.
- [x] **The turn budget reached through `Harness` into the SDK's `Agent`.**
  `2 * len(self._run.agent.tools)`, added while removing a hard-coded `8`,
  which is the worse of the two: `harness.py` is the only module that may
  know the SDK's shape. `test_harness.py` pins that nothing else *imports*
  `agents`, and an attribute walk goes straight past it. Counted off the list
  the responder built.
- [x] Each tool carried a dead docstring — written on the inner function,
  then overwritten by a `__doc__` assignment that no dynamic text needed — so
  the source a human read was not what the model got. One docstring each.
- [x] The tools' parameter names are asserted. Nothing pinned them: the
  factories were checked for `.name` and their behaviour through the library
  underneath, which keeps passing if a tool asks for the wrong thing.
