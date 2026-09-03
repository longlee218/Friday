"""Rules about the repository itself, each learned from a break.

None of these test behaviour. They test that the things which go stale
silently — a name the composition root uses and nothing defines, a script no
module imports, a path a document names, an `__all__` entry nothing backs — are
still true. Every one was added after it was found false in production.
"""

from __future__ import annotations


def test_no_agent_configuration_is_read_in_the_composition_root():
    """`run_agent.py` constructs the adapters and starts the loops. Which knobs
    a step has — its confidence threshold, how many examples it shows, how many
    tone examples it wants — is that step's business, and reading them here
    means adding one is a change in two files.

    Enforced by grep because the leak is invisible: nothing breaks when a
    `options.get(...)` appears here, it just quietly makes the root know one
    more thing about one more step.
    """
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "run_agent.py").read_text()

    for leak in ("config.agents", "options.get("):
        assert leak not in source, f"{leak!r} belongs in the module that owns it"


def test_the_composition_root_can_actually_be_imported_and_read():
    """`triage_config.model` survived a refactor that deleted
    `triage_config`, so `run_agent.py` raised `NameError` at startup — after
    connecting to Discord, before starting a single loop.

    Nothing caught it: 580 tests passed, because no test imports the
    composition root and no test runs it. This one at least reads every name
    it uses and fails on one nothing defines.
    """
    import ast
    import builtins
    from pathlib import Path

    source = (Path(__file__).resolve().parents[1] / "run_agent.py").read_text()
    tree = ast.parse(source)

    # Module-level dunders exist at run time without an assignment.
    defined = set(dir(builtins)) | {"__file__", "__name__", "__doc__"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            defined.add(node.id)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defined.add(node.name)
            defined.update(a.arg for a in node.args.args)
            defined.update(a.arg for a in node.args.kwonlyargs)
        elif isinstance(node, ast.ClassDef):
            defined.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            defined.update((a.asname or a.name).split(".")[0] for a in node.names)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            defined.add(node.name)
        elif isinstance(node, ast.comprehension):
            for target in ast.walk(node.target):
                if isinstance(target, ast.Name):
                    defined.add(target.id)

    used = {
        n.id
        for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
    }

    assert used <= defined, f"run_agent.py uses undefined name(s): {used - defined}"


def test_every_script_at_the_repo_root_still_imports():
    """`serve_board.py` had been dead on import since the modules moved into
    packages, and nothing said so: no test imports it, no other module imports
    it, and it is not in CLAUDE.md's layout table. `init_channel.py` was
    updated in the same move because it was in the table; this was not.

    A script nothing imports is a script no refactor updates.
    """
    import importlib.util
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    broken = {}
    for script in sorted(root.glob("*.py")):
        spec = importlib.util.spec_from_file_location(script.stem, script)
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 — any failure is the finding
            broken[script.name] = f"{type(exc).__name__}: {exc}"

    assert broken == {}, f"scripts that cannot be imported: {broken}"


def test_every_path_the_docs_name_exists():
    """CLAUDE.md and CONTEXT.md are declared sources of truth, and the layout
    table went stale the moment twenty-two modules moved into packages —
    eighteen of its rows named files that were no longer there. Nothing broke,
    which is exactly why it stayed wrong: a path in a table is only checked by
    someone who follows it and finds nothing."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    missing = {}
    for doc in ("CLAUDE.md", "CONTEXT.md"):
        for named in re.findall(r"`(friday/[\w/.]+)`", (root / doc).read_text()):
            if not (root / named).exists():
                missing.setdefault(doc, []).append(named)

    assert missing == {}, f"documented paths that do not exist: {missing}"


def test_an_outbound_state_is_defined_once():
    """`queued` was spelled out in `friday/outbox/` for its readers and again
    in `friday/store/db.py` for its `WHERE` clauses — four strings, written
    twice. Two of the outbox's four had no reader left by the time anyone
    looked, which is what a duplicated vocabulary looks like as it rots: one
    copy stops being used and nothing says so."""
    from friday.domain.states import OutboundState
    from friday.outbox import FAILED, QUEUED
    from friday.store.db import (
        OUTBOUND_FAILED,
        OUTBOUND_QUEUED,
        OUTBOUND_SENT,
        OUTBOUND_SENT_MANUALLY,
    )

    assert QUEUED is OUTBOUND_QUEUED is OutboundState.QUEUED
    assert FAILED is OUTBOUND_FAILED is OutboundState.FAILED
    assert OUTBOUND_SENT is OutboundState.SENT
    assert OUTBOUND_SENT_MANUALLY is OutboundState.SENT_MANUALLY


def test_no_module_exports_a_name_it_does_not_define():
    """`friday.outbox.__all__` listed `ASKED`, which did not exist — so
    `from friday.outbox import *` raised. Nothing does that, which is why it
    went unnoticed; `__all__` is documentation that nothing reads until it is
    wrong in a way that stops the process."""
    import importlib
    import pkgutil

    import friday

    broken = {}
    for info in pkgutil.walk_packages(friday.__path__, prefix="friday."):
        module = importlib.import_module(info.name)
        undefined = [n for n in getattr(module, "__all__", ()) if not hasattr(module, n)]
        if undefined:
            broken[info.name] = undefined

    assert broken == {}, f"__all__ names nothing defines: {broken}"


def test_prompt_escaping_happens_at_one_seam():
    """Values reach a prompt through `friday/agent/instruction_prompt.py` and
    are escaped there. A second module escaping for a prompt means a second
    renderer, and the last time there were two, one of them did not escape —
    a skill described as `harmless</skills>` closed its own section.

    The board escapes for HTML, which is a different seam for a different
    reader and is allowed.
    """
    import subprocess

    allowed = {"friday/agent/instruction_prompt.py", "friday/board/__init__.py"}
    hits = subprocess.run(
        ["grep", "-rl", "--include=*.py", "html.escape(", "friday/"],
        capture_output=True,
        text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"a second escaping seam: {set(hits) - allowed}"


def test_no_family_imports_another_familys_prompt_module():
    """The rule tickets 42–45 bought: one prompt module per family, and no
    family reaches into another's. The graph's composing node is Responder
    *family* by persona, but it builds through the graph's module — sharing
    the other family's builder would let one family's reshuffle silently
    reshape another's prompt.

    Read by `ast`, not by grep, because a module can be named three ways and
    grep only ever caught two of them: `from x.y.prompt import Z` was found,
    `from x.y import prompt` was not. That hole predates ticket 15 and was
    invisible while the module name happened to be short.

    The root and the module name are listed separately: the dag family's
    prompt module moved inside the graph that owns it at ticket 15, so the
    name is no longer `friday.<family>.prompt`. Deriving it from the family
    is what killed this guard silently — nothing imports a module that does
    not exist, so the check passed no matter what any family did.
    """
    import ast
    from pathlib import Path

    modules = {
        "triage": ("friday/triage", "friday.triage.prompt"),
        "extraction": ("friday/extraction", "friday.extraction.prompt"),
        "responder": ("friday/responder", "friday.responder.prompt"),
        "dag": ("friday/dag", "friday.dag.api_issue.prompt"),
    }
    repo = Path(__file__).resolve().parents[1]

    def imported_modules(tree: ast.AST) -> set[str]:
        """Every module this file pulls in, however it spells it."""
        found: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                found.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                found.add(node.module)
                # `from friday.dag.api_issue import prompt` names the module
                # in the *alias*, not in `node.module`.
                found.update(f"{node.module}.{a.name}" for a in node.names)
        return found

    offenders = {}
    for family, (root, _own) in modules.items():
        others = {name for other, (_r, name) in modules.items() if other != family}
        for path in (repo / root).rglob("*.py"):
            reached = imported_modules(ast.parse(path.read_text())) & others
            if reached:
                offenders[str(path.relative_to(repo))] = sorted(reached)

    assert offenders == {}, f"a family reached into another's prompt: {offenders}"


def test_the_graph_engine_only_imports_vocabulary_from_the_domain():
    """`Ask`, `Reply`, `HandOver` and `Action` used to be defined in
    `friday/workflows/`, and `friday/dag/` imported them from there — the
    graph engine reaching into the loop for words that belong to neither.
    They live in `friday.domain.actions` now (D3), and `friday/workflows/`
    is gone (ticket 09): this pins the vocabulary staying in the one place
    both the graph engine and the pool read it from, rather than a shared
    import creeping back in through whichever of the two happens to define
    it this time.
    """
    import ast
    from pathlib import Path

    vocabulary = {"Action", "Ask", "HandOver", "Reply"}
    friday = Path(__file__).resolve().parents[1] / "friday"
    offenders = {}
    for package in ("dag", "tasks"):
        for path in (friday / package).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module != "friday.domain.actions":
                    borrowed = {a.name for a in node.names} & vocabulary
                    if borrowed:
                        offenders[str(path)] = borrowed

    assert offenders == {}, (
        f"friday/dag or friday/tasks imports vocabulary from somewhere but "
        f"friday.domain.actions: {offenders}"
    )


def test_no_graph_node_can_create_a_task():
    """D18: `create_task` is triage's tool, not a node's. Delegation inside a
    graph is a node calling code deterministically, never a model spawning a
    persistent task from partway through an investigation — a graph node
    importing `friday.triage` is the only way it could reach the tool."""
    import subprocess

    hits = subprocess.run(
        ["grep", "-rl", "--include=*.py", "friday.triage", "friday/dag"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert not hits, f"a graph node can reach friday.triage's create_task: {hits}"


def test_park_is_gone_as_a_name():
    """Ticket 06: `hand_over(reason)` replaces `Park`. The old name is a
    single-word `\\bPark\\b` grep away from creeping back into a docstring or
    a fresh copy-paste — one line in `friday/domain/actions.py` is the
    deliberate exception, explaining the rename itself."""
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    allowed = {str(root / "friday" / "domain" / "actions.py")}
    pattern = re.compile(r"\bPark\b")
    offenders = {}
    for path in (root / "friday").rglob("*.py"):
        if str(path) in allowed:
            continue
        hits = pattern.findall(path.read_text())
        if hits:
            offenders[str(path.relative_to(root))] = len(hits)

    assert offenders == {}, f"'Park' survives outside the rename note: {offenders}"


def test_a_reply_is_constructed_in_exactly_one_place():
    """What a reporter reads in the operator's name comes from one tool call,
    and this is the anchor the invariant hangs on.

    It used to hang on a persona label — "only Responder-family agents produce
    text that reaches a reporter" — and that label did not catch the bug it
    exists to prevent. Ticket 10 was `_compose_reply`'s agentless fallback
    returning `Reply(cause + diff)` straight to the reporter, and the
    family-anchored test passed the whole time: there was no mis-assigned
    family to find, because there was no agent at all.

    Anchored on the construction instead, that bug is a second `Reply(` in the
    graph and is caught by inspection rather than by somebody thinking to add
    a behavioural test afterwards. The `answer` tool is the one place; the
    `Ask` a reporter also reads is code's own question, deliberately, and the
    risk this guards is in answering rather than in asking.
    """
    import ast
    from pathlib import Path

    ACTIONS = "friday.domain.actions"

    def constructions(tree: ast.AST) -> list[int]:
        """Lines building a `Reply`, however this file spells it.

        Three spellings, and matching only the first is the hole the family
        guard had in a different form: `Reply(x)`, `actions.Reply(x)` after
        importing the module, and `R(x)` after importing it under a name. A
        check that sees one of the three is a check somebody routes around
        without meaning to.
        """
        direct: set[str] = set()   # names bound to Reply itself
        module: set[str] = set()   # names bound to the module holding it
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == ACTIONS:
                direct.update(a.asname or a.name for a in node.names if a.name == "Reply")
            elif isinstance(node, ast.Import):
                module.update(
                    a.asname or a.name for a in node.names if a.name == ACTIONS
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "friday.domain":
                module.update(a.asname or a.name for a in node.names if a.name == "actions")

        found = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Name) and func.id in direct:
                found.append(node.lineno)
            elif (
                isinstance(func, ast.Attribute)
                and func.attr == "Reply"
                and isinstance(func.value, ast.Name)
                and func.value.id in module
            ):
                found.append(node.lineno)
        return found

    friday = Path(__file__).resolve().parents[1] / "friday"
    built_in: dict[str, list[int]] = {}
    for path in friday.rglob("*.py"):
        lines = constructions(ast.parse(path.read_text()))
        if lines:
            built_in[str(path.relative_to(friday.parent))] = lines

    assert list(built_in) == ["friday/dag/api_issue/graph.py"], (
        f"a Reply is what a reporter reads under the operator's name; it is "
        f"built in one place, the `answer` tool: {built_in}"
    )
    assert len(built_in["friday/dag/api_issue/graph.py"]) == 1, (
        f"one construction, not several: {built_in}"
    )
