"""Pin the CLAUDE.md status paragraph to the current board.

A future edit that drops the pointer to
`.scratch/a-monitor-on-the-whole-path/` without updating the docs
is a regression the operator would catch at the next board
review. The test catches it first.
"""
from __future__ import annotations

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
