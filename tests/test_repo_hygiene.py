"""Pin the CLAUDE.md status paragraph to the current board, and
regression guards learned from earlier breaks.

A future edit that drops the pointer to
`.scratch/a-monitor-on-the-whole-path/` without updating the docs
is a regression the operator would catch at the next board
review. The test catches it first.

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


def test_claudemd_status_mentions_the_current_monitor_board() -> None:
    """CLAUDE.md names the active board. If it points at the wrong
    one (Liquid Glass / SSE old board), the docs are stale — and
    the audit's rule says CLAUDE.md is the first place a fresh
    agent looks."""
    src = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "a-monitor-on-the-whole-path" in src, (
        "CLAUDE.md does not point at .scratch/a-monitor-on-the-whole-path/. "
        "The status paragraph is stale."
    )


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


def test_claudemd_records_the_sse_decision() -> None:
    """Ticket 10 acceptance criterion 3: an Architecture-constraints
    bullet records the D5 (SSE) decision. A future edit that
    removes the bullet is a regression the audit would catch —
    CLAUDE.md would drift from the actual behaviour, and the
    next agent to read it would not know the bus is the seam."""
    src = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    assert "EventBus" in src or "Event Bus" in src, (
        "CLAUDE.md no longer names the EventBus — the SSE seam "
        "is undocumented"
    )
    assert "/api/events" in src, (
        "CLAUDE.md no longer mentions /api/events — the SSE endpoint "
        "is undocumented"
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
    """The audit named two files every agent reads first: CLAUDE.md
    and `docs/DESIGN.md`. A regression that renames a section or
    drops a path the docs reference is a regression the build
    catches first."""
    docs = ROOT / "CLAUDE.md"
    src = docs.read_text(encoding="utf-8")
    # A backticked path under one of this repo's own directories, or one of
    # the two entrypoint scripts, must exist on disk.
    #
    # **The directory prefix has to be followed by a separator**, and that is
    # the whole of what this pattern got wrong for a while: without it,
    # `friday` matched the *dotted module* names this file writes on purpose
    # (`friday.triage.prompt`), and `docs` matched the first four letters of
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
        f"CLAUDE.md names paths that do not exist: {missing}"
    )

