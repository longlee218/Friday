# prompts/

Every agent's instructions, one file per prompt. This file is the only one in
the directory that is **never sent to a model** — everything else ships
verbatim, so the files themselves cannot carry comments. Read this before
editing.

**The wording is yours; the names in it are not.** Several prompts carry
contracts with the code that parses the model's output or assembles the rest of
the prompt:

- `dag/analyze_stack.md` names the JSON keys `cause`, `actionable`, `evidence`
  — the parser reads exactly those.
- `extractor.md` promises "JSON only, with the schema fields as keys" — the
  parser depends on it.
- `responder.md` names the sections `channel_overrides` / `channel_derived` /
  `channel_base`, the `people:` and `register` keys, the `<counterpart>`
  section, and the `fetch_skill` tool — all produced by code.
- `responder-counterpart.md` is injected as the `<counterpart>` section when
  writing to somebody the operator has no history with. The pronouns in it are
  the one thing meant to be tuned.

Editing takes effect on restart. A deleted or unreadable file stops the process
at startup, on purpose: an agent with empty instructions is broken, not
degraded.

What is *not* here: `PERSONA.md` (who the agents are — root of the repo),
`skills/` (operator knowledge, fetched on demand), and the extraction field
descriptions (they live on the fields in `friday/domain/models.py`, so a field
and its meaning cannot drift apart).
