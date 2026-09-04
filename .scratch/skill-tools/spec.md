# Spec: skill tools for the agents

Status: ready-for-agent.

## Problem Statement

Skills today are reachable through one tool, `fetch_skill`, and a one-line
catalogue in the prompt. That is the right shape for a small library, but it
hits a wall as the library grows:

- **Finding is by name, not by need.** The catalogue lists every skill by
  name and description, and the agent picks one — but only because the
  description happens to mention the words the reporter used. A skill named
  `deploy` whose description says "release a build" will be missed by a
  reporter who asks about "rolling out". The agent has no way to *search*; the
  catalogue is the only index.
- **The catalogue cost is in the prompt on every call.** A hundred skills at
  one line each is a page; two hundred is two. Stable-first ordering hides
  this for now, but it does not go away: every prompt pays for every skill
  regardless of whether the work needed one.
- **There is no structured view of a skill before the body lands.** The
  model reads `fetch_skill(name)` and gets the body, but not the metadata
  that would tell it what the skill is for, whether it is editable, where it
  lives, or what tools it expects. Body or nothing.
- **Supporting files are referenced by `name/file.md` and nothing else.** A
  body that points to a deep path inside its own folder — `references/setup.md`
  — is reachable, but the agent has to know to ask for the exact name; there
  is no way to browse.

The user's reference shape — `_render_skill_metadata` — names four fields
the model wants to see before reading the body: description with a
mutability tag, the tools the skill is allowed to use, the location on
disk, and a clear name. Our `Skill` carries two of those (name, description)
and not the other two (mutability, allowed-tools), because our skills are
not gated today and are all operator-written. Closing that gap is part of
this spec.

## Solution

Three tools, layered on top of the existing `SkillLibrary`:

- **`search_skills(query)`** — find skills whose name or description matches
  the query; matches are ranked, and the result is a short list of
  `name: description` lines. The body is never returned here.
- **`describe_skill(name)`** — return the structured metadata for one skill:
  name, description (with the mutability tag), the allowed-tools list (or
  `(all)` if none was declared), and the absolute path to its `SKILL.md`.
  Body is not returned here either.
- **`read_skill_file(name, file_path)`** — return the contents of a file
  inside a skill's directory, looked up by the path the body referred to.
  This is the third step of disclosure, given its own name so the body can
  point at deep paths (`references/setup.md`) without the model having to
  concatenate.

The catalogue in the prompt stays. It becomes the fast path for skills the
agent already knows about; the search tool becomes the way to find skills
the catalogue does not surface. Removing the catalogue would cost every
call a search turn for skills the model already saw by name, which is the
wrong trade.

`fetch_skill` stays. Its job — body or supporting file in one call — is
narrower than the three new tools together, and an agent that already knows
the name has no reason to search then describe then read.

## User Stories

1. As the operator, I want every agent that reasons to have three skill
   tools — search, describe, read — so that an agent can find a skill it
   cannot name, see what it is for, and read what it points at.
2. As the operator, I want the search tool to match against the description,
   not the body, so that a skill's prose does not have to be loaded into the
   prompt to be found.
3. As the operator, I want search results ranked, so that an exact name hit
   beats a description-only hit, and a description hit beats a token match.
4. As the operator, I want `describe_skill` to show me, as the model, four
   fields: name, description with the mutability tag, allowed tools, and
   location — in the same `key: value` shape the catalogue and
   `channel_overrides` use, so the agent does not have to learn a new
   format.
5. As the operator, I want a skill's mutability to be in its frontmatter,
   so the metadata rendered by `describe_skill` cannot drift from what the
   operator wrote.
6. As the operator, I want a skill's allowed-tools to be in its
   frontmatter, so the model knows what the skill expects before it fetches
   the body — and so a skill that wants only a log reader does not get a
   write tool handed to it.
7. As the operator, I want the location field in `describe_skill` to be the
   absolute path to the skill's `SKILL.md`, so I can see exactly which file
   I am about to read.
8. As the operator, I want every skill that ships with the repo to round-
   trip through the new tools, so the existing `skills/` directory keeps
   working without edits.
9. As the operator, I want `read_skill_file` to be scoped to a skill's own
   directory, so a fabricated path like `deploy/../../.env` cannot escape
   it — the same property the existing `fetch_skill` already has.
10. As the operator, I want the catalogue in the prompt to keep working
    alongside the new tools, so an agent that already knows the name still
    fetches in one call instead of three.
11. As the operator, I want a tool that does not exist for this agent to
    leave the prompt unchanged — no orphan description that teaches the
    agent to look for a door that is not in the room.
12. As the operator, I want every agent that reasons to be told in its
    prompt which of the three tools it has, so an agent without the search
    tool is not asked to search.
13. As the operator, I want triage to keep its `stop_on_first_tool`
    behaviour, so adding skill tools does not turn a one-turn classification
    into a multi-turn investigation.
14. As the operator, I want an extractor to keep its one-shot behaviour,
    so a skill that would have helped is at worst missed — not a
    multi-step detour that delays the report.
15. As the responder, I want to call `search_skills("correlation")` and get
    back `where-to-find-a-correlation-id`, so I can fetch it without having
    remembered the exact name.
16. As the responder, I want `describe_skill("where-to-find-a-correlation-
    id")` to tell me whether the skill is editable and where it is, so I
    can judge whether to trust what it says without burning a fetch.
17. As the responder, I want to call `read_skill_file("deploy",
    "references/rollback.md")` and get the rollback file, so a skill that
    splits into several markdown files is browsable the way a person would
    browse it.
18. As a maintainer, I want the three tools to live in `friday/tools/`,
    one module per subject, so the answer to "what can the agents do?"
    stays in one place.
19. As a maintainer, I want a test that asserts the tool list, like
    `test_the_tools_this_system_has_are_all_in_one_place` does today, so
    adding a fourth tool changes the test and nothing else.
20. As a maintainer, I want the catalogue in the prompt to be byte-identical
    before and after this change, because the cache hit on the stable
    prefix is what makes the catalogue affordable in the first place.
21. As a maintainer, I want no test to reach into the matching or ranking
    logic directly, so the algorithm can change (substring → token, token →
    BM25) without rewriting the suite.
22. As a maintainer, I want the `Skill` dataclass to grow two optional
    fields — mutability and allowed-tools — both defaulting to safe values,
    so existing skills parse without edits.
23. As a maintainer, I want `SkillLibrary` to expose what the new tools
    need and nothing they do not, so the tools do not start depending on
    internals.

## Implementation Decisions

### Tools

- **D1. Three new tools, one per subject, in `friday/tools/`.**
  - `search_skills.py` — `search_skills_tool(library)`; closes over the
    library; returns a ranked list of `name: description` lines.
  - `describe_skill.py` — `describe_skill_tool(library)`; closes over the
    library; returns the structured metadata block.
  - `read_skill_file.py` — `read_skill_file_tool(library)`; closes over the
    library; returns the file's contents from `Skill.files`.

  Factories rather than module-level tools, because the library is the
  injection site — the same shape `fetch_skill` already has. What an agent
  can reach is composition.

- **D2. Tool names are `search_skills`, `describe_skill`, `read_skill_file`.**
  Names matter: a tool name is an instruction to the model. `search_skills`
  is plural because the result is a list; `describe_skill` mirrors the
  reference shape the operator proposed; `read_skill_file` mirrors the
  operator's `read_file` request while making the scope explicit in the
  name itself — "skill file", not "file", because the tool is scoped to
  a skill's directory.

- **D3. The tool list test (`test_the_tools_this_system_has_are_all_in_one
  _place`) grows by three.** Adding a tool changes that line and nothing
  else. The shape of the test stays: imports the package, walks the tools
  directory, asserts the names.

### Skill metadata

- **D4. `Skill` grows two optional frontmatter fields, both with safe
  defaults.**
  - `mutability: Literal["built_in", "custom"]` — defaults to `"custom"`
    because today's skills are all operator-written and editable; the
    default is the truth about what exists today.
  - `allowed_tools: tuple[str, ...]` — defaults to `()` (interpreted as
    "(all)" by `describe_skill`, matching the reference shape).
  Both default to forward-compatible shapes: a skill with no new fields in
  its frontmatter parses without change.

- **D5. Frontmatter values are escaped at the seam.** The reference code
  escapes `name`, `description`, `allowed-tools`, and `location` with
  `html.escape(..., quote=False)`. Our existing escape pattern is the same;
  a value that closes its own block is the same injection class. The
  escape belongs in `SkillLibrary`'s reader, not in the tool's renderer.

- **D6. `describe_skill`'s output uses the same `key: value` shape as the
  catalogue.** One skill, four lines, no heading, blank line between
  skills when `search_skills` returns more than one:
  ```
  name: <name>
  description: <description> [custom, editable]
  allowed_tools: <tools> or (all)
  location: <absolute path>
  ```
  The catalogue's line shape is `<name>: <description>`; this is the
  multi-line extension of that same `key: value` format, in the same
  YAML-like style `_render_yaml_escaped` already produces for
  `channel_overrides` and `channel_derived`. The mutability tag is
  appended to the description line in the two strings the reference
  shows — `built-in` or `custom, editable` — so a model trained on the
  reference shape still recognises the signal.

### Search

- **D7. `search_skills` matches against name and description, never the
  body.** The body is what the agent reads *after* deciding a skill is
  relevant; using the body to find a skill would put the body in two
  places — the index and the fetch — and the body is the expensive one.
  A description that does not say the right words is the operator's
  description, not the tool's problem.

- **D8. Ranking: exact name > name prefix > description substring > token
  match.** Exact-name beats prefix beats substring beats token, because
  the operator names skills deliberately and the agent that asked for
  the name should find it first. The ranking is a small list of four
  rules; the test asserts the order, not the score, so the algorithm can
  be swapped (substring → token, token → BM25) without rewriting the suite.

- **D9. Search returns up to five matches.** Five is enough to cover any
  reasonable query against the shipped library; a result that long is
  the agent's signal to narrow the query, not the tool's signal to keep
  all of them. The cap is in the tool, not the library.

- **D10. An empty query returns the empty list.** A query with no
  matches returns a sentence saying what was searched and what is
  available. Same shape as `fetch_skill`'s "no skill called" line.

### Read

- **D11. `read_skill_file(name, file_path)` is the third step of
  disclosure, given its own name.** It is what `fetch_skill(name/file.md)`
  already does — but only when the model knows to concatenate. A skill
  that refers to `references/setup.md` in prose is reachable today; the
  agent has to know the `name/file.md` shape. The new tool lets the body
  point at a deep path with the path verbatim.

- **D12. The lookup is `Skill.files[file_path]`, exact match.** No
  filesystem I/O at fetch time — files are read at startup, served from
  memory. The same property the existing `fetch_skill` has, which is
  what makes a fabricated path unservable: there is no directory to
  traverse.

- **D13. A path the library never catalogued returns "no file" with the
  list of what was.** Same error shape as `fetch_skill(name/file.md)`'s
  "no file" answer.

### Prompt

- **D14. The catalogue in the prompt stays.** Two paths cost one extra
  turn on the search route; removing the catalogue would cost one extra
  turn on the known-name route, and known-name is the common case. The
  catalogue's `skill_system` renderer is unchanged.

- **D15. Each agent is told only about the tools it has.** A section that
  describes a tool renders only when the agent has it — the same rule
  `clarification_system` and `memory_tool_system` already follow. An
  agent without `search_skills` is not asked to search; an agent without
  `describe_skill` is not asked to describe.

- **D16. The new tool descriptions live in `instruction_prompt.py`,
  behind a single renderer per tool.** The catalogue lives in one
  renderer today; three new sections would have been three more
  copy-pastes if each agent's prompt built them by hand. They don't:
  the renderer takes a flag for each tool and renders nothing if the
  flag is false.

### Wiring

- **D17. The responder gets all three new tools.** The responder is the
  agent that already has `fetch_skill`; the three new tools are the same
  capability with finer grains. No new agent gets any of them in this
  spec.

- **D18. Triage, extractors, and graph nodes do not get the new tools in
  this spec.** Adding them to triage would change `stop_on_first_tool`;
  adding them to extractors would turn one-shot extraction into
  multi-step; adding them to graph nodes is a separate ticket because
  it changes which agent has which capability. Each is a separate
  decision, made when its case is open.

- **D19. The new `tool(fn)` decorators respect the existing rule.**
  `tests/test_tools.py::test_no_tool_is_declared_outside_the_tools_
  package` reads the syntax, not the source — both `@tool` and
  `tool(fn)` are caught. The new tools use `tool(fn)` after `__doc__` is
  assigned, the same way `ask_clarification` does, because the docstring
  is built from the library.

### Stable front of the prompt

- **D20. The byte-identical prefix test from tickets 42–45 keeps
  passing.** The catalogue's content is unchanged; the new tool
  descriptions are appended after it and before `tone_examples`. Two
  calls that differ only in their per-call input share a longer
  byte-identical prefix than before, never a shorter one.

## Testing Decisions

A good test here verifies behaviour through an interface a caller uses,
not the shape of the code inside. The test should still pass if the
ranking is swapped from substring to BM25, if `Skill.files` becomes a
mapping of path → bytes, or if the catalogue is rendered with a
different escape. It should fail the moment a fabricated path is served,
a description goes out unescaped, or an agent is asked to use a tool it
does not have.

Three seams, all of which already exist. No new ones.

1. **`SkillLibrary` against a tmp directory.** The existing
   `tests/test_skills.py` exercises the reader, the catalogue, the
   fetch, the frontmatter parser, the supporting-files lookup, and the
   error reporting. The new tests for frontmatter mutability and
   allowed-tools live here, because they describe what a skill *is*.
   Specifically:
   - `mutability` and `allowed_tools` parse from frontmatter and
     default safely.
   - Escaping happens at the reader, not the tool.
   - The shipped skills round-trip through every new code path.

2. **`search_skills_tool` / `describe_skill_tool` /
   `read_skill_file_tool` against a tmp library.** Each tool is a
   factory closing over a library; the tests build the factory the same
   way `fetch_skill_tool`'s tests do. Specifically:
   - `search_skills` ranks four cases in the order D8 says (exact,
     prefix, substring, token).
   - `search_skills` returns at most five matches.
   - `describe_skill` returns the four fields in D6's order.
   - `describe_skill` escapes the four fields it renders.
   - `read_skill_file` returns a file by deep path.
   - `read_skill_file` rejects paths not catalogued.
   - Each tool name appears in the agent it is wired into and does not
     appear in agents it is not.

3. **`tests/test_tools.py` for the cross-cutting rule.** The tool list
   grows by three; the no-tool-outside-`friday/tools/` rule still
   passes; the tool-shaped-but-not-a-tool guard still passes. The
   factory pattern is exercised the way `test_a_factory_tool_is_reachable_too`
   already does for `fetch_skill` and `ask_for_fields`.

Prior art: `tests/test_skills.py` for seam 1; `tests/test_tools.py` for
seam 3; the new tests for seam 2 follow the structure of
`test_the_tool_is_bound_to_one_library`.

The byte-identical-prefix test from tickets 42–45 is run for the
responder's prompt before and after this change; the catalogue section
is the same bytes, the new sections are appended at the back, and
`test_a_stable_prefix_lands_byte_for_byte` still passes.

Every guard added is checked by removing it and watching the suite go
red — the project rule, repeated because it is the one verification
that does not depend on what the test asserts.

## Out of Scope

- **Replacing `fetch_skill`.** Its job is narrower than the three new
  tools together, and an agent that already knows a name still wants
  the one-call path. `fetch_skill` stays.
- **Triage, extractor, or graph-node wiring.** D18 names this; each is
  its own ticket.
- **A general file reader.** `read_skill_file` is scoped to a skill's
  own directory. A `read_file` tool that could read arbitrary paths on
  disk is a different tool with different guards.
- **Re-ranking search with an embedding model.** The four-rule ranking
  is enough at the library's current size; an embeddings index is a
  separate decision.
- **Editing a skill.** `mutability: custom, editable` describes *that*
  a skill can be edited by the operator, not that the agent can. An
  agent that edits its own skills would edit its own instructions;
  out of scope.
- **Frontmatter validation of `allowed_tools`.** Today the field is a
  free-form list; a skill that names a tool the agent does not have
  gets `(all)` from the agent's perspective. Tightening this is a
  separate ticket because it needs every agent to publish its tool
  list, which is a new contract.

## Further Notes

The reference code's mutability and allowed-tools fields did not exist
in our `Skill`. They are being added because the spec's `describe_skill`
shape needs them, and the operator's reference is the source of truth for
what `describe_skill` should output. Adding fields with safe defaults
(D4) is the smallest change that lets the existing skills parse and the
new tools work.

The catalogue in the prompt is byte-identical before and after this
spec. The captured-prompt test from tickets 42–45 holds.

The three new tools are factories closing over a `SkillLibrary`; their
shape matches `fetch_skill_tool` and `ask_for_fields_tool` exactly, and
they go in `friday/tools/` one module per subject. The factory pattern
is the established seam; no new seam is proposed.