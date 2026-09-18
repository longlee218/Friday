"""What the system keeps between tasks: the summariser that writes each room's
`summary` row (`channel_context.py`) and the operator's verdict on a
classification (`verdicts.py`). Per-channel context the operator wrote was a
YAML file here until board `read-it-the-way-the-operator-does`, ticket 10; it
is memory rows in the store now, one table for every kind.

There was a third — staged observations promoted into notes once an approved
outcome corroborated them — and it is gone (ticket 09's D9): nothing wrote an
observation once `remember` left the tool list, so it promoted nothing for
months before it was dropped. Its replacement is not a tier here at all — an
agent's own memory now lives in `friday/tools/memory.py`, scoped per channel
and reached through a tool call, following the rule that every tool lives in
`friday/tools/` regardless of what state it touches.

Empty on purpose: importing any submodule runs this first, so whatever lives
here is paid for by every import of the package.
"""
