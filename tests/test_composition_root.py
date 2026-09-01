"""The composition root, checked without running it.

`_run` is two hundred lines of sequential assignment, and most of what it
builds is optional — a responder, a bot, an agent for workflow steps. An
optional branch that reads a name assigned further down is legal Python,
imports cleanly, passes every test, and raises `UnboundLocalError` on the
day someone turns that branch on. This is the check that would have caught
it while the branch was still off.
"""

from __future__ import annotations

import ast
import pathlib


def _read_before_assigned(fn: ast.AsyncFunctionDef | ast.FunctionDef) -> list[str]:
    """Names the function reads before it assigns them.

    Nested functions are skipped: their bodies run later, so reading a name
    defined below them is correct — `decided` closes over `db` that way.
    """
    assigned_later = {
        node.id
        for node in ast.walk(fn)
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store)
    }
    seen: set[str] = {a.arg for a in fn.args.args}
    problems = []

    for statement in fn.body:
        # Everything this statement binds counts as available within it: a
        # `for` target is read by its own body, and that is not the mistake
        # being looked for. What is being looked for spans statements.
        for node in ast.walk(statement):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
                seen.add(node.id)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                seen.add(node.name)

        for node in ast.walk(statement):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(node, ast.Name)
                and isinstance(node.ctx, ast.Load)
                and node.id in assigned_later
                and node.id not in seen
            ):
                problems.append(f"{node.id} (line {node.lineno})")

    return problems


def test_nothing_in_the_composition_root_is_read_before_it_is_built():
    source = pathlib.Path("run_agent.py").read_text()
    tree = ast.parse(source)
    run = next(
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "_run"
    )

    assert _read_before_assigned(run) == []
