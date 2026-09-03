"""Tools an agent may call, that are not one graph's private business.

`clarify.py` holds `ask_clarification`, which every agent that can stop and
ask uses — that is what makes it belong here rather than beside one caller.

CLAUDE.md said for months there was deliberately no `tools/` package, and the
reason it gave was the right one: *"Build one of these when a second caller
appears, not before."* A tool for every agent is that second caller. Tools
with exactly one caller stay where they are — `answer` and `apply_fix` are
still declared beside the node whose state they touch, because that is the
only place their guard can be enforced.

Empty on purpose: importing any submodule runs this first.
"""
