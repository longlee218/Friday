# 03: Wire the new tools into the responder

**What to build:** The responder — the only agent today with `fetch_skill`
— also gets `search_skills`, `describe_skill`, and `read_skill_file`,
and its prompt carries the matching sections. The catalogue section is
unchanged byte-for-byte; the three new sections sit behind it, before
the per-call data, so the stable prefix is unchanged and the cache hit
on it is preserved.

**Blocked by:** 02

**Status:** done

The wiring is the smallest change that lands the new tools with a real
caller. Triage, extractors, and graph nodes are intentionally not
addressed here — each is a separate decision, and any of them getting
the new tools is its own ticket. The responder is the one that already
has `fetch_skill`, so the three new tools are the same capability with
finer grain.

- [x] `friday/responder/__init__.py` builds the responder with the
      `fetch_skill_tool` plus the three new factories, all closing
      over the same `SkillLibrary` instance
- [x] `Responder.draft` raises `extra_turns` so an agent can chain
      search → describe → read → body fetch and still produce a reply
      in one call; the bump is the smaller of: enough turns for every
      tool call a reasonable responder makes, or a dynamic count from
      the libraries it has — whichever lands in one line
- [x] `friday/agent/instruction_prompt.py` adds three renderers —
    `search_skills_system`, `describe_skill_system`,
    `read_skill_file_system` — each taking a `bool` flag and rendering
      nothing when the agent does not have the tool, the same way
      `clarification_system` and `memory_tool_system` already do
- [x] `friday/responder/prompt.py::build_input` calls the three new
      renderers with `True`, in this order, after `skill_system` and
      before `tone_examples`
- [x] The catalogue section's bytes are unchanged: same lines, same
      order, same escape — the prefix test from tickets 42–45 still
      passes for the bytes before `tone_examples`
- [x] `tests/test_skills.py::test_the_responder_is_given_the_catalogue_and_the_tool`
      is updated: the tool list now has four names in
      `responder._run.agent.tools`, in declaration order
- [x] `tests/test_skills.py` adds a case asserting the responder prompt
      contains the section names when wired, and renders nothing when
      `skills is None` (the responder without skills carries no tools and
      no skill-related prompts, same as today)
- [x] Byte-identical prefix capture, taken before and after this
      ticket, matches for the bytes from the start of the prompt
      through the end of `skill_system`
- [x] Suite stays green: `uv run pytest -q`