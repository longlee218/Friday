"""Pin the docs to where each kind of fact now lives, and regression
guards learned from earlier breaks.

Since 2026-09-18 `CLAUDE.md` says only how to work: project state lives in
`CONTEXT.md` § Project state and architecture in `docs/DESIGN.md` § What
exists. The two content pins below follow their facts there — a pin left
on `CLAUDE.md` would force the state and architecture back into the file
the operator emptied of them.

The other tests in this file are learned-from-break guards:
composition root reads no agent config (the audit's rule),
scripts at the repo root still import, paths the docs name
exist, and the same module does not export a primitive twice.
These were the original 540-line `test_repo_hygiene.py`; that
file was overwritten by ticket 10's commit and the tests
moved to smaller per-guard files in `test_web_tokens.py` and
the local `test_repo_hygiene.py`. A subset lives here so the
history stays readable.
"""
from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _project_state() -> str:
    src = (ROOT / "CONTEXT.md").read_text(encoding="utf-8")
    return src[src.index("# Project state"):src.index("# Vocabulary")]


def _what_exists() -> str:
    src = (ROOT / "docs" / "DESIGN.md").read_text(encoding="utf-8")
    return src[src.index("# What exists"):src.index("# Reasoning")]


def test_project_state_mentions_the_current_monitor_board() -> None:
    """The project state names the monitor board. If it points at the wrong
    one (Liquid Glass / SSE old board), the state is stale — and it is the
    first place a fresh agent looks for what is running."""
    assert "a-monitor-on-the-whole-path" in _project_state(), (
        "CONTEXT.md § Project state does not point at "
        ".scratch/a-monitor-on-the-whole-path/. The state is stale."
    )


def test_claudemd_holds_no_state_and_no_architecture() -> None:
    """The operator's rule of 2026-09-18: CLAUDE.md is how to work, nothing
    else. These headings are the ones that used to live there and drifted
    there; one coming back is the file growing a second copy of what
    CONTEXT.md and docs/DESIGN.md already say."""
    src = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    for heading in ("## Status", "## Layout", "## Architecture constraints",
                    "## Running it on a server"):
        assert heading not in src, f"CLAUDE.md grew {heading!r} back"


def test_old_board_tickets_are_marked_retired_or_done() -> None:
    """Ticket 19 (Liquid Glass) is retired, ticket 20 (SSE) is done.
    A regression that leaves one in `ready-for-agent` is a regression
    the audit would catch on the next board review."""
    old19 = (ROOT / ".scratch" / "discord-mention-triage" / "issues" / "19-liquid-glass-on-the-chrome.md").read_text()
    old20 = (ROOT / ".scratch" / "discord-mention-triage" / "issues" / "20-live-updates-without-polling.md").read_text()
    assert "Status:** retired" in old19 or "Status:** done" in old19, (
        "ticket 19 should be marked retired or done"
    )
    assert "Status:** retired" in old20 or "Status:** done" in old20, (
        "ticket 20 should be marked retired or done"
    )


def test_design_records_the_sse_decision() -> None:
    """Ticket 10 acceptance criterion 3: a load-bearing constraint records
    the D5 (SSE) decision. A future edit that removes it is a regression —
    the architecture would drift from the actual behaviour, and the next
    agent to read it would not know the bus is the seam."""
    src = _what_exists()
    assert "EventBus" in src or "Event Bus" in src, (
        "docs/DESIGN.md § What exists no longer names the EventBus — the "
        "SSE seam is undocumented"
    )
    assert "/api/events" in src, (
        "docs/DESIGN.md § What exists no longer mentions /api/events — the "
        "SSE endpoint is undocumented"
    )


def test_composition_root_reads_no_agent_config() -> None:
    """The audit's rule: the composition root (`run_agent.py`) is
    the only place agent knobs are read. Anywhere else reaching
    for `config.yaml`'s `agents.*` section is a regression — the
    next agent to refactor will believe a value it has no right
    to."""
    tree = ast.parse((ROOT / "run_agent.py").read_text(encoding="utf-8"))
    offenders = []
    for node in ast.walk(tree):
        # A bare attribute access like `config.agents.triage.foo`
        # counts; the audit looks for any read of a model name
        # outside the composition root.
        if isinstance(node, ast.Attribute):
            chain = []
            cur = node
            while isinstance(cur, ast.Attribute):
                chain.append(cur.attr)
                cur = cur.value
            chain.reverse()
            if "agents" in chain:
                offenders.append(".".join(chain))
    assert not offenders, (
        f"run_agent.py reads agent config outside the composition "
        f"contract: {offenders}"
    )


def test_doc_paths_resolve_to_existing_files() -> None:
    """Every path the docs present as existing must exist: all of
    CLAUDE.md, CONTEXT.md § Project state and docs/DESIGN.md § What exists.
    DESIGN.md § Reasoning and CONTEXT.md § Vocabulary are left out on
    purpose — they name removed packages as history."""
    src = "\n".join([
        (ROOT / "CLAUDE.md").read_text(encoding="utf-8"),
        _project_state(),
        _what_exists(),
    ])
    # A backticked path under one of this repo's own directories, or one of
    # the two entrypoint scripts, must exist on disk.
    #
    # **The directory prefix has to be followed by a separator**, and that is
    # the whole of what this pattern got wrong for a while: without it,
    # `friday` matched the *dotted module* names this file writes on purpose
    # (`friday.kernel.triage.prompt`), and `docs` matched the first four letters of
    # `docstring_style`. Six false positives, none of them a path, and a red
    # suite that told every later change the repo was broken.
    import re
    candidates = re.findall(
        r"`((?:\.scratch|friday|docs|web|migrations|tests)/[^`]+"
        r"|run_agent\.py|serve_board\.py)`",
        src,
    )
    missing: list[str] = []
    for path_str in candidates:
        # A template, not a path: `.scratch/<feature-slug>/issues/` describes
        # where a board goes, and no such directory is meant to exist.
        if "<" in path_str:
            continue
        # A path this file says outright is not there yet. `docs/adr/` is
        # named as an absence — "does not exist yet" — and requiring it to
        # exist would be requiring the sentence to be wrong.
        if re.search(rf"`{re.escape(path_str)}`\s+does not exist", src):
            continue
        if not (ROOT / path_str).exists():
            missing.append(path_str)
    assert not missing, (
        f"the docs name paths that do not exist: {missing}"
    )



def test_only_config_knows_where_the_message_age_cutoff_lives() -> None:
    """`friday/kernel/config.py`'s `message_age_cutoff` says it is "the only place
    that knows where it lives", and until this test that was a sentence
    rather than a fact.

    Two readers share that number now — triage, which marks a turn
    `outdated`, and the sweep, which uses it as how far a cold cursor reads
    back. Neither may learn that it sits under `agents.triage`: the inbox
    would gain a dependency on triage's configuration section, and the
    composition root is forbidden agent config outright by the test above.
    Reaching for the key by name anywhere else is how that becomes untrue,
    so the key as a *string* may appear in one file.

    A parameter or keyword named `max_message_age` is not a string literal
    and is not what this catches — `TriageRunner` is free to call its own
    argument that, and does.
    """
    offenders: list[str] = []
    for path in sorted((ROOT / "friday").rglob("*.py")):
        if path.name == "config.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value == "max_message_age":
                offenders.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not offenders, (
        "the cutoff's config key is named outside friday/kernel/config.py: "
        f"{offenders}. Call `message_age_cutoff(config)` instead — see its "
        "docstring."
    )
