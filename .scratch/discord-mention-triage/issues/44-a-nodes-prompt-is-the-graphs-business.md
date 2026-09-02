# 44: A node's prompt is the graph's business

**What to build:** One module owning how a graph node's prompt is assembled:
the Node persona, the node's own text, the skills catalogue for reasoning
nodes, and the channel sections for the one node that writes to a person.
Today that assembly lives inside the wiring function that also builds agents,
picks servers and reads config — four jobs in one place.

**Blocked by:** None (can start immediately)

**Status:** done

## Acceptance criteria

- [x] `build_instructions(node, persona, skills)` in the graph's own prompt
      module; the wiring function wires and assembles nothing
- [x] The per-node user turns (the evidence handed to analyze, the said/fix
      handed to compose) stay in the node functions — they are the node's
      logic, not prompt furniture; the boundary is written down in the module
- [x] Output byte-identical to today's, captured not eyeballed
- [x] The existing family tests (voice in compose, no voice in analyze) pass
      untouched

## What it came to

`friday/dag/prompt.py`: `build_instructions(node, persona, skills)` plus the
`REASONING` set. The wiring function wires; the per-node user turns stayed in
the node functions, per the boundary in the ticket. Texts load at import so a
missing file fails at startup — lazily they had left the orphan-prompts test
passing on test-order luck. Byte-identical, ten of ten.
