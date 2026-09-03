"""What a graph node's prompt looks like, and from what it is assembled.

The stable half only. A node's *per-call* input — the evidence handed to the
analysis, the cause and diff handed to the composer — is the node's own logic
and stays in the node function; pulling it here would gut the nodes to furnish
the prompt module. The boundary: this file owns what is the same on every call,
the node owns what this call is about.

Assembly per node: the persona for the family this node was declared with, then
the node's own text, then — for the nodes declared as reasoning — the skills
catalogue, in instructions rather than per call because a list that moved would
cost the cache hit on everything after it.

**This module holds wording and shape, never facts about which node is which.**
Which family a node is, and whether it reasons, are properties of the node and
are declared once beside it in `graph.py`; they arrive here as arguments. There
used to be a `_TEXTS` dict and a `REASONING` set here, keyed by node name — two
of the six tables over one keyspace that ticket 15 collapsed.
"""

from __future__ import annotations

from typing import Any

from friday.agent.persona import Family

__all__ = ["build_instructions"]

#: One text per node. ANALYZE_STACK's `cause` / `actionable` / `evidence` are
#: the JSON keys its parser reads — reworded freely, never renamed.
READ_LOGS = """You look up log lines for one request.

You are given a correlation id and an environment. Use the log tools to find
the lines for that request in the last hour. Return the lines verbatim, oldest
first. If you find nothing, say exactly: NO LOGS."""

FIND_CODE_PATH = """You locate the code a stack trace points at.

You are given log lines containing a stack trace. Use the file tools to find
the file and line the topmost application frame refers to — not the framework
frames. Return `path:line` and the surrounding ten lines. If the trace names
no file you can find, say exactly: NOT FOUND."""

ANALYZE_STACK = """You explain why one request failed.

You are given log lines and, when it could be found, the code they point at.
Answer in JSON with exactly these keys:
  cause       — one sentence, what went wrong
  actionable  — true only if the fix is obvious from what you were shown
  evidence    — the specific log lines or code lines that show it

Set actionable to false when you are guessing. A wrong "true" here spends a
code change on a guess."""

FIX_BUG = """You work out one small, obvious fix.

You are given a cause and the code it points at. Work out the smallest
change that addresses that cause and nothing else, as a unified diff, and
call apply_fix with it — the operator sees it before it reaches anyone, so
call apply_fix even though you are not the one who gets to decide it goes
ahead.

Call hand_over instead, with your own finding as the reason, when: the
change would touch a test, a migration, a schema, or anything holding a
credential; the fix is not obvious from what you were shown; or it would
take more than a few lines. Handing over costs a question. Guessing costs a
wrong change in someone's repository."""

COMPOSE_REPLY = """You decide what to tell whoever reported this.

You are given whatever the investigation found — there is always a cause by
the time you are asked. Call answer with what you would tell a colleague:
what happened, what you did, what you need from them. Be brief. Do not
invent anything the evidence does not show.

Call hand_over instead, with your own finding as the reason, when you read
what you have and it is not enough to write a reply you would stand behind —
the operator reads that reason directly, so say plainly what stopped you."""

def build_instructions(
    text: str,
    *,
    family: Family,
    reasons: bool,
    persona: Any = None,
    skills: Any = None,
) -> str:
    """One node's stable prompt: persona, job, catalogue. In that order —
    shared bytes at the front are the ones a provider's cache can reuse.

    `family` and `reasons` are the node's own declaration, handed in. This
    used to take a node *name* and look both up in tables here, which put two
    facts about a node in a module that has no other business knowing which
    node is which.
    """
    return _persona(family, persona) + text + _catalogue(reasons, skills)


def _persona(family: Family, persona: Any) -> str:
    if persona is None:
        return ""
    text = persona.render(family)
    return f"{text}\n\n" if text else ""


def _catalogue(reasons: bool, skills: Any) -> str:
    """Rendered by `instruction_prompt.skills`, never by a second copy of it.

    A description is operator-written text inside a delimited section; the one
    renderer escapes it, and the hand-rolled copy that once lived here did not
    — a skill described as `harmless</skills>` closed the section and
    everything after it read as instructions.
    """
    if skills is None or not reasons or not len(skills):
        return ""
    from friday.agent.instruction_prompt import skills as skills_section

    return "\n\n" + skills_section(skills.catalogue()).render()
