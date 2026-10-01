"""What a command's exit code means, for the handful that use it to say more
than success/failure — Claude Code's own list
(`src/tools/BashTool/commandSemantics.ts`): `grep`/`rg` return 1 for no
matches, not an error; `find` returns 1 when some directories could not be
read; `diff` and `test`/`[` use 1 for a true comparison that just came out
the other way. Everything else is the default: 0 is success, anything else
is worth explaining as a failure.
"""

from __future__ import annotations

__all__ = ["exit_meaning"]

#: `{command: {exit code: what it means}}`. Only the codes worth a note —
#: 0 never needs one, and a code not listed here falls through to the caller's
#: own "exit N" line.
_MEANINGS: dict[str, dict[int, str]] = {
    "grep": {1: "no matches found, not an error"},
    "rg": {1: "no matches found, not an error"},
    "find": {1: "some directories were inaccessible"},
    "diff": {1: "files differ"},
    "test": {1: "condition is false"},
    "[": {1: "condition is false"},
}


def exit_meaning(command: str, code: int) -> str | None:
    """A note for `code` on `command`, or `None` when the default reading —
    0 is success, anything else is a failure — already says enough.

    `command`'s **last pipeline segment** is what sets `$?`, the same
    heuristic Claude Code uses: good enough for a note, not a guard, so it is
    never asked to refuse anything."""
    last = command.strip().rsplit("|", 1)[-1].strip()
    name = last.split()[0] if last.split() else ""
    return _MEANINGS.get(name, {}).get(code)
