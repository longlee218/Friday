# 01: `Skill` grows `mutability` and `allowed_tools`

**What to build:** A skill's frontmatter carries two new optional fields —
`mutability` and `allowed_tools` — so `describe_skill` can render them
without the tool having to invent values. Both default to safe shapes so
every skill that already ships with the repo loads unchanged.

**Blocked by:** None (can start immediately)

**Status:** done

The fields are read at startup, served from memory, and rendered by a
later ticket. This ticket is the prerequisite: nothing downstream of it
can land without the fields existing. Both fields are optional in the
frontmatter; a skill that does not declare them parses anyway, and the
defaults tell the truth about today's library — every shipped skill is
operator-written (so `custom`) and lets the agent use any tool (so `()`,
which `describe_skill` renders as `(all)`).

- [x] `Skill` has `mutability: Literal["built_in", "custom"]`, default `"custom"`
- [x] `Skill` has `allowed_tools: tuple[str, ...]`, default `()`
- [x] `SkillLibrary._read` parses `mutability` and `allowed_tools` from
      frontmatter when present; uses the defaults when absent
- [x] `SkillLibrary._read` rejects `mutability` values outside the two
      strings, naming the file in the error — same shape as every other
      frontmatter rejection
- [~] ~~`SkillLibrary._read` escapes the fields at the seam~~ —
      **superseded, see `04-review-fixes.md`.** Escaping in the reader
      would have put it in a store; it belongs with the renderer, and
      the renderer belongs in `instruction_prompt`. The reader parses
      and validates and escapes nothing
- [x] Every skill that ships in `skills/` round-trips through the new
      reader with `library.problems == []`
- [x] `tests/test_skills.py` adds cases for: defaults applied, value
      rejected on bad `mutability`, escape at the seam on `<` and `&`
- [x] `tests/test_skills.py` adds a case asserting the shipped library
      parses with `mutability == "custom"` and `allowed_tools == ()`
- [x] Suite stays green: `uv run pytest -q`