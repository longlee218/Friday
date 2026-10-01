"""The hook ticket 27 fills: whether a command needs the operator's approval
before it runs.

Decided 2026-09-30: no deny list. A sensitive command waits for the operator
rather than being refused outright — that flow, and what makes a command
sensitive, is ticket 27's. This ticket ships `bash` already calling the hook,
before every command, so 27 only has to change what is behind it: `False`
for everything, for now.
"""

from __future__ import annotations

__all__ = ["sensitive"]


def sensitive(command: str, host: str) -> bool:
    return False
