# 07: Verbatim material becomes an artifact the build points at

**What to build:** Code, stack traces, SQL, logs and configuration are stored
whole and pointed at, never summarised. A reporter's `curl` has to reach the
person who will run it exactly as typed, with its line breaks, after any amount
of summarisation has happened around it — and a correlation id is matched by
machine, so a paraphrase of it resolves to nothing.

The module that splits code out of prose already exists for this reason, and its
docstring names the extractor as the cause.

**Blocked by:** 01

**Decisions:** D2, D8

**Status:** ready-for-agent

- [ ] Material split out of prose is stored as an artifact with an id and one
      line of description
- [ ] The build renders an artifact as its id and that line; an artifact
      belonging to the current task is inlined whole
- [ ] No summariser is ever shown an artifact's content, and a test says so
- [ ] The reader of an artifact cannot itself produce one, so a large artifact
      read back cannot spill into a second
- [ ] A `curl` a reporter pasted survives with its line breaks end to end
- [ ] A correlation id reaches the parameters character for character
- [ ] Guards deleted once and watched go red
