"""Every tool this system has, in one place — and a way to list them.

The operator asked which tools exist and where they are loaded. Answering by
grepping `@tool` was wrong twice over: two of them are wrapped by calling
`tool(...)` after `__doc__` is assigned, so the decorator never appears, and
the ones that did appear were scattered across four modules that each owned
part of the answer.

So the rule is `friday/tools/` holds them all, and these tests are what make
that a rule rather than a tidy-up somebody will undo. A tool declared beside
its caller is invisible to the next person asking the same question.
"""

from __future__ import annotations

import ast
import importlib
import pkgutil
from pathlib import Path

import friday.tools

REPO = Path(__file__).resolve().parents[1]
TOOLS = REPO / "friday" / "tools"


def _tool_objects() -> dict[str, object]:
    """Every SDK tool reachable from the package, by the name the model sees.

    Imports and inspects rather than reading source, because that is the only
    way to catch both spellings — and the model sees the *tool's* name, which
    is not always the Python name that produced it.
    """
    found: dict[str, object] = {}
    for info in pkgutil.iter_modules([str(TOOLS)]):
        module = importlib.import_module(f"friday.tools.{info.name}")
        for attr in vars(module).values():
            if hasattr(attr, "name") and hasattr(attr, "params_json_schema"):
                found[attr.name] = attr
    return found


def test_the_tools_this_system_has_are_all_in_one_place():
    """The list, asserted rather than described. A new tool changes this line
    and nothing else — which is the point: the question "what can the agents
    do?" has one answer with one place to read it."""
    assert set(_tool_objects()) == {
        "ask_clarification",
        "answer",
        "hand_over",
        "apply_fix",
        "create_task",
        "skip",
    }


def test_no_tool_is_declared_outside_the_tools_package():
    """Both spellings, by reading the syntax rather than grepping: `@tool`
    above a function, and `tool(fn)` called on one. Grep saw the first and
    missed the second, which is how two of these went unlisted."""
    offenders: dict[str, list[int]] = {}
    for path in (REPO / "friday").rglob("*.py"):
        if path.is_relative_to(TOOLS) or path.name == "harness.py":
            continue
        tree = ast.parse(path.read_text())
        lines = []
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef):
                for dec in node.decorator_list:
                    target = dec.func if isinstance(dec, ast.Call) else dec
                    if isinstance(target, ast.Name) and target.id == "tool":
                        lines.append(node.lineno)
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "tool"
            ):
                lines.append(node.lineno)
        if lines:
            offenders[str(path.relative_to(REPO))] = lines

    assert offenders == {}, f"a tool declared outside friday/tools/: {offenders}"


def test_a_factory_tool_is_reachable_too():
    """`fetch_skill` and `ask_for_fields` are built per-call — one is bound to
    a skill library, the other to one type's field names — so they are not
    module-level objects and the listing above cannot see them. They are still
    tools, and still here; this is what says so."""
    from friday.agent.skills import SkillLibrary
    from friday.domain.models import ApiIssueParams
    from friday.tools.ask_for_fields import ask_for_fields_tool
    from friday.tools.fetch_skill import fetch_skill_tool

    built = fetch_skill_tool(SkillLibrary(REPO / "skills"))
    fields = ask_for_fields_tool(ApiIssueParams)

    assert built.name == "fetch_skill"
    assert fields.name == "ask_for_fields"


def test_the_two_asking_tools_are_not_the_same_tool():
    """They answer different questions, and collapsing them would trade a
    constraint the code can check for a shorter list. `ask_for_fields` offers
    a closed enum of one type's own fields, so the model cannot name one that
    does not exist; `ask_clarification` takes a question in words, which is
    right when nobody knows in advance what might be unclear."""
    from friday.domain.models import ApiIssueParams
    from friday.tools.ask_for_fields import ask_for_fields_tool
    from friday.tools.clarify import ask_clarification

    fields = ask_for_fields_tool(ApiIssueParams).params_json_schema["properties"]
    words = ask_clarification.params_json_schema["properties"]

    assert "fields" in fields and "question" not in fields
    assert "question" in words and "fields" not in words
    assert fields["fields"]["items"]["enum"], "the field names are a closed set"
