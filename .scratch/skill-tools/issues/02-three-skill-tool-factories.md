# 02: Three tool factories in `friday/tools/`

**What to build:** Three agent-callable tools — `search_skills`,
`describe_skill`, `read_skill_file` — wired to a `SkillLibrary` at build
time. An agent that has them can find a skill it cannot name, see what
the skill is for, and read the file the body points at; an agent that
does not have them still has `fetch_skill` and the catalogue.

**Blocked by:** 01

**Status:** done

Three tools, three files, one subject each — the rule that
`tests/test_tools.py` enforces already, extended by three names. Each
factory closes over a `SkillLibrary` the way `fetch_skill_tool` does
today. `SkillLibrary` grows two public methods — `search(query)` and
`metadata_for(name)` — so the tools do not reach into `Skill` internals;
nothing is added that another caller does not call.

- [x] `friday/tools/search_skills.py` defines `search_skills_tool(library)`
      that returns a tool named `search_skills` taking `query: str` and
      returning a ranked list of `name: description` lines, ranked by
      exact-name > name-prefix > description-substring > token-match,
      capped at 5 matches
- [x] `friday/tools/describe_skill.py` defines `describe_skill_tool(library)`
      that returns a tool named `describe_skill` taking `name: str` and
      returning the four-line `key: value` block: `name`, `description`
      (with `[built-in]` or `[custom, editable]` appended), `allowed_tools`
      (or `(all)`), `location` (absolute path to the skill's `SKILL.md`)
- [x] `friday/tools/read_skill_file.py` defines `read_skill_file_tool(library)`
      that returns a tool named `read_skill_file` taking `name: str` and
      `file_path: str` and returning the file's contents from
      `Skill.files` — rejects any path that was not catalogued at
      startup, naming what was
- [x] `SkillLibrary.search(query) -> str` returns ranked `name: description`
      lines (newline-joined) using the same ranking rules; an empty
      query returns `""`, a no-matches query returns a sentence naming
      what was searched and what is available
- [~] ~~`SkillLibrary.metadata_for(name) -> str` returns the four-line
      block~~ — **superseded, see `04-review-fixes.md`.** The block is
      `instruction_prompt.skill_metadata`; the library supplies `get`,
      `location_of` and `known`, and `describe_skill.describe` composes
      them. The unknown-name sentence is unchanged
- [x] `Skill` carries the path it was loaded from so `metadata_for`
      can render the absolute path in `location`
- [x] No new public attribute is added to `Skill` that the tools do not
      read; the four renderable fields are what the tools render
- [x] `tests/test_tools.py::test_no_tool_is_declared_outside_the_tools_package`
      still passes (the rule reads both spellings)
- [x] `tests/test_tools.py::test_a_factory_tool_is_reachable_too` adds
      assertions for the three new factories, mirroring the
      `fetch_skill_tool` check — each factory builds a tool whose name
      matches what the model sees
- [x] `tests/test_skills.py` adds cases for: search ranking in the four
      specified orders; search empty query returns `""`; search
      no-match returns a sentence; search is case-insensitive; search
      caps at 5; describe output order and mutability tag; describe
      escapes `<` and `&` at the seam; describe location is an
      absolute path; describe unknown name returns a sentence;
      read deep path (`references/setup.md`); read uncatalogued path
      returns the "no file" sentence, not the file
- [x] Suite stays green: `uv run pytest -q`