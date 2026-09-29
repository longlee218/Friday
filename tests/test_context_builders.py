"""The gather modules every prompt family's builder lives in.

Board `what-the-room-already-knows`, D26: each family that gathers anything
has exactly one gather function, beside its prompt module, returning one
frozen value that reads but never writes, never imports a section builder,
never constructs a `Section`, and never joins anything. This is that rule's
guard — the same shape `test_prompt_sections.py` already holds prompt
modules to, one level stricter.

Ticket 14 gives this file its first entry (`friday/kernel/triage/context.py`);
ticket 15 adds the second (`friday/kernel/extraction/context.py`). The list is
written out, not derived from a glob or from the family names — the lesson
CLAUDE.md records from ticket 15 of the first board: a module name derived
from its family let a family-enumerating test silently stop checking when
the module moved.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path


def _context_modules() -> list[Path]:
    """Every module that gathers a family's context. Written out by hand."""
    root = Path(__file__).resolve().parents[1] / "friday"
    return [
        root / "kernel" / "triage" / "context.py",
        root / "kernel" / "extraction" / "context.py",
    ]


def test_every_gather_module_reads_and_never_renders():
    """D4's rule, held more strictly than a prompt module is: a gather
    module may not import anything from the section-builder seam, may not
    construct a `Section`, and may not call `assemble` or join rendered
    output. A prompt module may do the first two; this module may not do
    any of the three."""
    offenders: dict[str, list[str]] = {}
    for path in _context_modules():
        tree = ast.parse(path.read_text())

        imports_seam = any(
            isinstance(node, ast.ImportFrom)
            and node.module == "friday.kernel.harness.instruction_prompt"
            for node in ast.walk(tree)
        )
        if imports_seam:
            offenders.setdefault(path.name, []).append(
                "imports from friday.kernel.harness.instruction_prompt"
            )

        calls = {
            node.func.id
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        if "Section" in calls:
            offenders.setdefault(path.name, []).append("constructs a Section")
        if "assemble" in calls:
            offenders.setdefault(path.name, []).append("calls assemble")

        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "join"
            ):
                offenders.setdefault(path.name, []).append(
                    f"joins something at line {node.lineno}"
                )

    assert offenders == {}, f"a gather module renders instead of reading: {offenders}"


def test_the_list_of_gather_modules_is_a_literal_not_a_derivation():
    """Not merely "returns the right answer today" — a `glob("*/context.py")`
    returns exactly the same single path this literal does while there is
    only one gather module in the tree, and would keep passing right up
    until a second one existed and a rename or a move made the two disagree.
    Inspected at the source: `_context_modules` may build its list from
    literal `Path` expressions only — no `glob`, `rglob`, `iterdir`, `walk`,
    or any other call that discovers files instead of naming them."""
    tree = ast.parse(inspect.getsource(_context_modules))
    disallowed = {"glob", "rglob", "iterdir", "walk", "scandir", "listdir"}

    found = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr in disallowed
    }

    assert found == set(), f"the module list is derived by {found}, not written out"
    assert [p.name for p in _context_modules()] == ["context.py", "context.py"]
    assert [p.parent.name for p in _context_modules()] == ["triage", "extraction"]
    assert all(p.exists() for p in _context_modules()), (
        "a listed gather module does not exist"
    )
