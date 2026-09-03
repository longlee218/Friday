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
    reshape another's prompt."""
    import subprocess

    modules = {
        "triage": "friday/triage",
        "extraction": "friday/extraction",
        "responder": "friday/responder",
        "dag": "friday/dag",
    }
    for family, root in modules.items():
        others = [f"friday.{m}.prompt" for m in modules if m != family]
        for other in others:
            hits = subprocess.run(
                ["grep", "-rl", "--include=*.py", other, root],
                capture_output=True,
                text=True,
            ).stdout.strip()
            assert not hits, f"{family} imports {other}: {hits}"


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


def test_only_responder_family_agents_can_speak_for_the_operator():
    """The invariant new to ticket 09: only Responder-family agents produce
    text that reaches a reporter. A node inside an investigation is a step,
    not a voice — give the wrong node the Responder persona and every test
    that checks *whether* a reply gets sent still passes; only its *tone*
    quietly changes, under the operator's name, to something they never
    wrote. Nothing else in the suite would catch that, which is exactly why
    this is pinned directly rather than left to be implied by behaviour.

    Two things have to both hold: `Family.RESPONDER` is only ever named
    inside `friday/responder/` (the operator's own voice) and in the one node
    declaration that claims it; and building from that declaration really
    does send `compose_reply` the Responder section and every other node the
    Node section.

    Since ticket 15 the family is a field on a node's own declaration rather
    than a branch on its name in a shared prompt module, so the first half
    reads a table and the second builds from it. Same two questions.

    **This half only checks wiring, and wiring is not the whole invariant.**
    Text can reach a reporter without passing through an agent at all: the
    composing node's own fallback replied with the analysis's prose and a raw
    diff whenever no agent was configured, and this test passed the entire
    time, because there was no mis-wired family to find. The behavioural half
    lives in `tests/test_dag_api_issue.py` —
    `test_an_unconfigured_composer_does_not_reply_in_a_nodes_voice` and
    `test_an_unconfigured_composer_never_puts_a_diff_in_front_of_a_reporter`
    (ticket 10). Neither half is sufficient alone.
    """
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "friday"
    allowed = {
        root / "responder" / "__init__.py",
        root / "responder" / "prompt.py",
        root / "dag" / "api_issue" / "graph.py",
    }
    pattern = re.compile(r"Family\.RESPONDER")
    offenders = {}
    for path in root.rglob("*.py"):
        if path in allowed:
            continue
        hits = pattern.findall(path.read_text())
        if hits:
            offenders[str(path.relative_to(root.parent))] = len(hits)
    assert offenders == {}, (
        f"Family.RESPONDER used outside friday/responder/ and the graph's "
        f"own node declarations: "
        f"{offenders} — only the composer and the operator's own Responder may "
        f"write in that voice"
    )

    from friday.agent.persona import Family
    from friday.dag.api_issue.graph import NODES
    from friday.dag.api_issue.prompt import build_instructions

    class _Spy:
        def render(self, family: Family) -> str:
            return "RESPONDER" if family is Family.RESPONDER else "NODE"

    claimed = {n for n, spec in NODES.items() if spec.family is Family.RESPONDER}
    assert claimed == {"compose_reply"}, (
        f"only the node that answers a reporter may claim that voice: {claimed}"
    )

    for node, spec in NODES.items():
        voice = build_instructions(
            spec.prompt, family=spec.family, reasons=spec.reasons, persona=_Spy()
        )
        expected = "RESPONDER" if node == "compose_reply" else "NODE"
        assert voice.startswith(expected), (
            f"{node} was built with the wrong persona family — only "
            f"compose_reply may speak in the operator's voice"
        )
