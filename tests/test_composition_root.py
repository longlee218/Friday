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


#: Every builder in `_run` that ends up holding a `Harness`. **Written out,
#: not derived.** Deriving it is how the prompt-families test stopped checking
#: anything when a module moved: a list that computes itself agrees with
#: whatever the code happens to be.
AGENT_BUILDERS = (
    "register_extractors",   # the three extractors
    "TriageRunner.build",    # triage
    "Responder.build",       # the responder
    "ContextRebuilder.build",  # the summariser
)


def _calls_in_run() -> dict[str, ast.Call]:
    """Every call `_run` makes, by the dotted name it was made under."""
    source = pathlib.Path(__file__).resolve().parents[1] / "run_agent.py"
    tree = ast.parse(source.read_text())
    run = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_run"
    )

    def named(func: ast.expr) -> str | None:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            return f"{func.value.id}.{func.attr}"
        return None

    found = {}
    for node in ast.walk(run):
        if isinstance(node, ast.Call) and (name := named(node.func)):
            found[name] = node
    return found


def test_every_agent_is_built_with_somewhere_to_record():
    """D1: the sink is handed over at construction, and the composition root
    is the only place it comes from.

    The sink has to be a name, not merely a keyword: `record=None` at all four
    call sites is the whole system recording nothing, and that is what this
    board exists to fix. And one name, not four — a second sink would be a
    second answer to "where does this go".

    It was a `calls=` list passed to `Harness.run`, and three of the four
    callers forgot it — so `model_calls` held triage alone while the board
    described it as holding every prompt. Forgetting is the failure mode this
    guards, so the guard has to be mechanical: every builder that ends up
    holding a `Harness` is named above, and each one has to be handed the
    sink here.
    """
    calls = _calls_in_run()
    missing, sinks = [], set()
    for builder in AGENT_BUILDERS:
        node = calls.get(builder)
        if node is None:
            missing.append(f"{builder} is not called in _run at all")
            continue
        given = next((kw.value for kw in node.keywords if kw.arg == "record"), None)
        if given is None:
            missing.append(f"{builder} is built without a record sink")
        elif not isinstance(given, ast.Name):
            # Presence is not enough. `record=None` everywhere is the system
            # recording nothing, which is the bug this board exists to fix,
            # and a check that only asks whether the keyword was spelled would
            # pass straight through it.
            missing.append(f"{builder} is handed {ast.dump(given)}, not a sink")
        else:
            sinks.add(given.id)

    assert missing == [], "; ".join(missing)
    assert len(sinks) == 1, f"one sink, not {len(sinks)}: {sorted(sinks)}"


def test_the_sink_is_built_once_and_only_here():
    """One sink, in the composition root, so no module below it decides
    whether a call is worth keeping. `record_model_call` is the store's write;
    nothing outside `_run` may reach for it."""
    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = {
        str(path.relative_to(root))
        for path in (root / "friday").rglob("*.py")
        if "record_model_call" in path.read_text()
    } - {"friday/store/db.py"}

    assert offenders == set(), f"a second place decides to record: {offenders}"
