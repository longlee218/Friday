# 15: Node 0's full context is gathered in one place

**What to build:** Everything the extractor is shown about a task is gathered
by one function, returned as one value, threaded down as one object, and
rendered from that object alone. Today the full build is split across two
places that do not know about each other: node 0 fetches the transcript —
with its budget, its cooldown and its ineffective-compaction bookkeeping —
and the extractor's own `would_ask` fetches the room, the domain memories
and the open questions. Ticket 08 added `known` and had to thread it through
five signatures to get it from the first place to the second. That is the
shape D26 exists to end: one gather, one value, and a single point at which
"what did this task's extraction see, and why" can be logged and inspected
before it becomes a prompt.

Follows 14 by convention rather than by dependency: the module placement,
the value shape, the log line and the guards are decided there on the
smaller family, and this ticket applies them to the larger one.

**Blocked by:** 14

**Decisions:** D2, D3, D4, D5, D6, D7, D8, D26

**Status:** ready-for-agent

- [ ] A `FullContext` value exists — `transcript` (what the reporter has said,
      already under budget, or nothing), `room`, `domain_memories`, `asked`
      (the questions this task has asked and not had answered), `known` (the
      task's parameters as they stand) — frozen, carrying nothing else, and
      named `transcript` rather than `text` so it cannot be mistaken for one
      message's body
- [ ] One function, `build_full_context`, beside the extraction family's
      prompt module, returns it; node 0 calls it once per pass and is the
      only caller in production
- [ ] **The builder reads and never writes.** It takes the budget, consults
      the cooldown itself (a read), fetches the transcript under the
      effective budget, and reports on the value whether the transcript is
      still over budget. Recording an ineffective compaction — the one write
      on this path — stays in node 0, decided from that field. This is what
      lets a fingerprint, a test or a probe call the builder with no side
      effect, and it is asserted: a test calls the builder against an
      over-budget task and checks nothing was recorded
- [ ] One object travels the whole path: node 0's `prepare` takes the
      `FullContext` where it took `text`; the `extract` callable — both the
      remembering wrapper and the family's own — takes it; `Extractor.run`
      and `would_ask` take it; `build_input` takes it and nothing else. The
      five-signature `known` threading from ticket 08 collapses into one
      field of one object, and the ticket says so where that threading was
      documented
- [ ] `input_fingerprint` hashes what `build_input` renders from the same
      object node 0 will hand to the model — the fingerprint and the call can
      no longer be built from two separate gathers, which is a stronger
      version of the guarantee ticket 01 established
- [ ] Every guard ticket 08 added still holds through the object: the budget
      truncates oldest-first and keeps the newest, the count cap still binds
      under a generous budget, two ineffective compactions stop a third, the
      cooldown gate skips the check, a filled field drops out of the schema,
      an empty string does not count as filled, and `known` still moves the
      fingerprint. Each is re-run as a mutation on the new path, not assumed
      to have survived
- [ ] **Contract:** the extractor no longer holds a store or a context store
      of its own — both reach the builder from node 0's own dependencies,
      where the context store already travels. The registration function
      loses those two parameters and the composition root stops passing
      them. Nothing else in the extractor changes: the harness, the params
      class, the parse, the hygiene, the clarification capture are untouched
- [ ] The docstring on `Extractor.run` that argued for threading a string
      rather than a dict "through four layers" is reversed in the same
      commit and says why: the object is the point, and it is one object,
      not a dict
- [ ] The prompt is byte-identical before and after for every case the
      existing suite exercises — a room fact, an open question, a domain
      memory, a filled field, a budget-truncated transcript — asserted by
      building both ways and comparing for equality, not by re-running the
      classifier evaluation, which this ticket does not touch
- [ ] One log line per build, at debug level, as in 14: transcript length
      in characters and estimated tokens, whether it is over budget, whether
      a room was present, how many domain memories, how many open questions,
      which fields are already known — counts, sizes and field *names*, never
      a value or a message body
- [ ] The list of gather modules written in 14 gains its second entry — the
      literal list, not a glob, not a derivation from the family names — and
      the same `ast` rule holds for this module: no section-builder import,
      no section, no join
- [ ] CLAUDE.md is corrected in the same commit: the ticket-08 paragraph
      that describes `known` travelling five signatures, the ticket-01
      paragraph on where the room is looked up, the layout table's
      `friday/extraction/` row, and the `ExtractionMark` note on what the
      fingerprint covers
- [ ] **DAG-ready, not DAG-resident — deliberately.** `FullContext` is the
      value story 44 wants a later node to read from the run's own state,
      and this ticket shapes it for that (frozen, data only, no store
      handle) — but does **not** write it into `DAGState`. No second node
      exists to read it, and a value in state with no reader is the
      seam-without-a-consumer D2 forbids. The next piece of DAG work adds
      the line in node 0 that puts it into state *and* the node that reads
      it, together; D26's closing paragraph records the two constraints
      that ticket inherits. This ticket's Comments say so, so the boundary
      is a decision on record rather than an omission
- [ ] Guards deleted once and watched go red: the builder being bypassed for
      any one of its five inputs, the read-only rule (a write inside the
      builder), the fingerprint being computed from a second gather, the
      byte-identity test, the `ast` rule, the module list, and the log line
