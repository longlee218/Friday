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
    from friday.domain.tasks import OutboundState
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


def test_every_prompt_file_is_loaded_by_something():
    """Prompt texts live in `prompts/` and code loads each by name. A file
    nobody loads is a prompt somebody will edit and wait forever for the
    change to show — the exact failure moving them out of the code was meant
    to end. README.md is the one file meant for people, never for models."""
    from pathlib import Path

    # importing the four families loads every prompt they use
    import friday.dag.api_issue  # noqa: F401
    import friday.extraction  # noqa: F401
    import friday.responder  # noqa: F401
    import friday.triage  # noqa: F401
    from friday.agent.prompts import _DIR, loaded

    on_disk = {
        str(p.relative_to(_DIR).with_suffix(""))
        for p in Path(_DIR).rglob("*.md")
        if p.name != "README.md"
    }

    assert on_disk == set(loaded()), (
        f"orphaned prompt files: {on_disk - set(loaded())}; "
        f"loaded from nowhere: {set(loaded()) - on_disk}"
    )
