"""What a graph node's prompt looks like, and from what it is assembled.

The stable half only. A node's *per-call* input — the evidence handed to the
analysis, the cause and diff handed to the composer — is the node's own logic
and stays in the node function; pulling it here would gut the nodes to furnish
the prompt module. The boundary: this file owns what is the same on every call,
the node owns what this call is about.

Assembly per node: its family's persona (the composer writes to a person and is
Responder family; every other node is a step and gets Node), then the node's
own text from `prompts/dag/`, then — for the nodes that reason — the skills
catalogue, in instructions rather than per call because a list that moved would
cost the cache hit on everything after it.
"""

from __future__ import annotations

from typing import Any

from friday.agent.persona import Family
from friday.agent.prompts import prompt as _text

__all__ = ["REASONING", "build_instructions"]

#: The nodes that decide what evidence means, as opposed to fetching it.
#: "How to trace a request" is written down for whoever is deciding what the
#: logs mean, not for the thing fetching them — so only these get the
#: catalogue and the fetch tool.
REASONING = frozenset({"analyze_stack", "compose_reply"})

_TEXTS = {
    "read_logs": "dag/read_logs",
    "find_code_path": "dag/find_code_path",
    "analyze_stack": "dag/analyze_stack",
    "fix_bug": "dag/fix_bug",
    "compose_reply": "dag/compose_reply",
}

# Loaded at import, like every prompt: a missing file fails at startup, named,
# rather than when the graph first builds its agents.
for _name in _TEXTS.values():
    _text(_name)
del _name


def build_instructions(node: str, *, persona: Any = None, skills: Any = None) -> str:
    """One node's stable prompt: persona, job, catalogue. In that order —
    shared bytes at the front are the ones a provider's cache can reuse."""
    return _persona(node, persona) + _text(_TEXTS[node]) + _catalogue(node, skills)


def _persona(node: str, persona: Any) -> str:
    if persona is None:
        return ""
    family = Family.RESPONDER if node == "compose_reply" else Family.NODE
    text = persona.render(family)
    return f"{text}\n\n" if text else ""


def _catalogue(node: str, skills: Any) -> str:
    """Rendered by `instruction_prompt.skills`, never by a second copy of it.

    A description is operator-written text inside a delimited section; the one
    renderer escapes it, and the hand-rolled copy that once lived here did not
    — a skill described as `harmless</skills>` closed the section and
    everything after it read as instructions.
    """
    if skills is None or node not in REASONING or not len(skills):
        return ""
    from friday.agent.instruction_prompt import skills as skills_section

    return "\n\n" + skills_section(skills.catalogue()).render()
