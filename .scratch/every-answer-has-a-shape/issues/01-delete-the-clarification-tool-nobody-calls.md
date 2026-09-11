# 01: Delete the clarification tool nobody calls

**What to build:** nothing new — a prefactor. `ask_clarification` and the capture
it writes into have no caller and have never had one; the tools package's own
docstring says so ("kept for one"). D14 says dead capture machinery goes in this
work rather than being left for a caller that never came, because every reader
who meets it has to work out that nothing calls it. Doing it first means the
capture-removal tickets that follow are reading one pattern, not two.

**Blocked by:** None (can start immediately).

**Status:** ready-for-agent

- [ ] The clarification tool, its capture and its type vocabulary are gone from `friday/tools/`.
- [ ] The asserted tool list in `tests/test_tools.py` is one shorter, edited deliberately rather than derived.
- [ ] Nothing in `friday/` imports the deleted names; the suite is green.
- [ ] `friday/tools/__init__.py`'s docstring no longer describes a door that is not in the room.
