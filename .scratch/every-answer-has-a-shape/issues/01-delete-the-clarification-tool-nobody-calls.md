# 01: Delete the clarification tool nobody calls

**What to build:** nothing new — a prefactor. `ask_clarification` and the capture
it writes into have no caller and have never had one; the tools package's own
docstring says so ("kept for one"). D14 says dead capture machinery goes in this
work rather than being left for a caller that never came, because every reader
who meets it has to work out that nothing calls it. Doing it first means the
capture-removal tickets that follow are reading one pattern, not two.

**Blocked by:** None (can start immediately).

**Status:** done

- [x] The clarification tool, its capture and its type vocabulary are gone from `friday/tools/`.
- [x] The asserted tool list in `tests/test_tools.py` is one shorter, edited deliberately rather than derived.
- [x] Nothing in `friday/` imports the deleted names; the suite is green.
- [x] `friday/tools/__init__.py`'s docstring no longer describes a door that is not in the room.

## Comments

Deleted `friday/tools/clarify.py` whole — `ask_clarification`, `Clarification`,
`ClarifyCapture`, `CLARIFICATION_TYPES` — and `tests/test_clarify_tool.py` with
it. The asserted tool list went from thirteen to twelve, edited by hand as that
test requires.

`test_the_two_asking_tools_are_not_the_same_tool` contrasted the deleted tool
with `ask_for_fields`. What survived is the half that was load-bearing: the
field names an extractor may ask about are a closed set. Mutated
(`Literal[askable]` → `str`) and watched go red.

One thing worth knowing for anyone reading `clarification_system` in
`friday/agent/instruction_prompt.py`: it shares a word with the deleted tool
and is a different thing — the prompt section, parameterised by however the
agent actually asks. It stayed, and ticket 08 changed what it takes.

## Review

`/code-review` against `b61a7e1`, Standards and Spec as parallel subagents.
Ten findings, every one verified against the code before acting — see the
commit `Review fixes: ten findings from the two-axis review`. Four were real
defects: a construction-time `TypeError` that would have been a boot loop when
an agent declared both `answers=` and its own `tool_choice`; the responder
losing its guarantee that a memory it writes is attributed to the responder;
the run's state not travelling at all when an agent had no memory tools; and
the tools-package exemption matching by basename rather than by path. D8's
recording-sink half had not been done. Each fix carries a test, deleted once
and watched go red.
