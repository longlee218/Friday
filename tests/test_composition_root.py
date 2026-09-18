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
import builtins
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
#: The keywords every one of them must be handed. Two now, and the second was
#: added a ticket later and forgotten here — the test that exists precisely to
#: catch a forgotten wire read one keyword while a second was being threaded
#: through the same four calls, three lines away.
SEAMS = ("record", "spent")

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

    Every seam in `SEAMS`, not just the first one somebody wrote this for.

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
    missing = []
    per_seam: dict[str, set[str]] = {seam: set() for seam in SEAMS}
    for builder in AGENT_BUILDERS:
        node = calls.get(builder)
        if node is None:
            missing.append(f"{builder} is not called in _run at all")
            continue
        for seam in SEAMS:
            given = next((kw.value for kw in node.keywords if kw.arg == seam), None)
            if given is None:
                missing.append(f"{builder} is built without {seam}")
            elif not isinstance(given, (ast.Name, ast.Attribute)):
                # Presence is not enough. `record=None` everywhere is the
                # system recording nothing, which is the bug this board exists
                # to fix, and a check that only asks whether the keyword was
                # spelled would pass straight through it.
                missing.append(f"{builder}'s {seam} is {ast.dump(given)}, not a name")
            else:
                per_seam[seam].add(ast.dump(given))

    assert missing == [], "; ".join(missing)
    for seam, names in per_seam.items():
        assert len(names) == 1, f"one {seam}, not {len(names)}"


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


def test_the_responder_is_given_the_store_its_memory_tools_need():
    """`Responder.build(db=...)` is what lets it reach `memory_search` and the
    rest — the obvious-first agent for ticket 09's D9. Not part of `SEAMS`
    above: triage and the summariser have no opinion on this yet, so it is
    the responder's own seam, not a rule for every builder.
    """
    node = _calls_in_run().get("Responder.build")
    assert node is not None, "Responder.build is not called in _run at all"

    given = next((kw.value for kw in node.keywords if kw.arg == "db"), None)
    assert isinstance(given, ast.Name), (
        f"Responder.build is handed {ast.dump(given) if given else 'nothing'} "
        "for db, not a name"
    )


def test_every_name_a_module_level_coroutine_uses_is_one_it_can_see():
    """`serve_board` is called from `_run` but defined beside it, so a local
    of `_run` referenced inside it is a `NameError` at runtime and nothing
    earlier than runtime says so — not mypy, not the suite, because nothing
    imports and runs this function.

    Caught exactly this while wiring the context store into `build_api`: the
    call read `context_store=context_store` inside `serve_board`, where the
    name is `_run`'s local. It is a parameter now.
    """
    source = pathlib.Path(__file__).resolve().parents[1] / "run_agent.py"
    tree = ast.parse(source.read_text())
    module_level = {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    }
    for node in tree.body:
        if isinstance(node, ast.Import):
            module_level |= {(a.asname or a.name).split(".")[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom):
            module_level |= {a.asname or a.name for a in node.names}
        elif isinstance(node, ast.Assign):
            module_level |= {
                t.id for t in node.targets if isinstance(t, ast.Name)
            }

    for func in tree.body:
        if not isinstance(func, ast.AsyncFunctionDef) or func.name == "_run":
            continue
        args = func.args
        visible = set(module_level) | {
            a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)
        }
        if args.vararg:
            visible.add(args.vararg.arg)
        if args.kwarg:
            visible.add(args.kwarg.arg)
        for inner in ast.walk(func):
            if isinstance(inner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                visible.add(inner.name)
                visible |= {a.arg for a in inner.args.args}
            elif isinstance(inner, ast.Import):
                visible |= {
                    (a.asname or a.name).split(".")[0] for a in inner.names
                }
            elif isinstance(inner, ast.ImportFrom):
                visible |= {a.asname or a.name for a in inner.names}
            elif isinstance(inner, ast.Assign):
                visible |= {t.id for t in inner.targets if isinstance(t, ast.Name)}
            elif isinstance(inner, ast.comprehension) and isinstance(
                inner.target, ast.Name
            ):
                visible.add(inner.target.id)
            elif isinstance(inner, ast.withitem) and isinstance(
                inner.optional_vars, ast.Name
            ):
                visible.add(inner.optional_vars.id)

        used = {
            n.id
            for n in ast.walk(func)
            if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
        }
        unseen = used - visible - set(dir(builtins))
        assert not unseen, f"{func.name} reads names it cannot see: {sorted(unseen)}"
