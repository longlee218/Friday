"""`track_progress.py`: status prose read by rule, hand-written rows kept.

The two properties worth a test are the ones a careless edit breaks silently:
a rule order that lets "done except X" read as done, and a sync that throws
away the measurements nobody can regenerate.
"""

from __future__ import annotations

import json

import pytest

import track_progress as tp


@pytest.mark.parametrize(
    "line, expected",
    [
        ("done", "done"),
        ("done 2026-09-21", "done"),
        ("built and **switched off** (2026-09-22).", "done"),
        ("its four questions are answered (2026-09-22)", "done"),
        ("done except the `[db]` step, which is ticket 05's", "part-done"),
        ("part done (2026-09-22). D1–D14 are indexed", "part-done"),
        ("the half that matters is done (2026-09-22)", "part-done"),
        ("done (eval reading owed, gated on ticket 03)", "part-done"),
        ("re-scoped 2026-09-22, not started.", "not-started"),
        ("ready-for-human", "blocked"),
        ("**a precondition now** (2026-09-22)", "blocked"),
        ("superseded — reopened as 28", "withdrawn"),
        ("retired — superseded by 32 and 33", "withdrawn"),
        # Superseded in passing is not a withdrawn ticket.
        ("done (2026-09-22). Two of the bullets below are superseded", "done"),
        ("built, switched off, and **paused**", "blocked"),
        # A passing mention of a state is not that state.
        ("done, no longer blocked", "done"),
        ("precondition met, done", "done"),
        ("something nobody wrote a rule for", "unknown"),
        (None, "unknown"),
    ],
)
def test_a_status_line_is_read_by_rule(line, expected):
    assert tp.classify(line) == expected


def test_a_ticket_row_comes_from_the_file(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "ROOT", tmp_path)
    board = tmp_path / ".scratch" / "a-board" / "issues"
    board.mkdir(parents=True)
    ticket = board / "03-the-thing.md"
    ticket.write_text(
        "# The thing\n\nLabels: ready-for-agent\n\n**Status:** done except one step (2026-09-20)\n",
        encoding="utf-8",
    )

    row = tp.read_ticket(ticket, "a-board")

    assert row["ticket"] == "03"
    assert row["title"] == "The thing"
    assert row["status"] == "part-done"
    assert row["label"] == "ready-for-agent"
    assert row["updated"] == "2026-09-20"
    assert row["path"] == ".scratch/a-board/issues/03-the-thing.md"


def test_sync_rebuilds_tickets_and_keeps_every_hand_written_row(tmp_path, monkeypatch):
    scratch = tmp_path / ".scratch"
    issues = scratch / "a-board" / "issues"
    issues.mkdir(parents=True)
    (scratch / "a-board" / "spec.md").write_text(
        "# Spec\n\nWhat the board is for.\n", encoding="utf-8"
    )
    (issues / "01-first.md").write_text(
        "# First\n\n**Status:** done\n", encoding="utf-8"
    )
    (issues / "02-second.md").write_text(
        "# Second\n\n**Status:** not started\n", encoding="utf-8"
    )
    progress = scratch / "progress.jsonl"
    hand = [
        {
            "kind": "board",
            "board": "a-board",
            "summary": "written by hand",
            "dates": "2026-09-01 to 2026-09-02",
        },
        {"kind": "ticket", "board": "a-board", "ticket": "01", "status": "not-started"},
        {
            "kind": "measurement",
            "board": "a-board",
            "date": "2026-09-18",
            "what": "8 lines in 8.5 s",
            "source": "x",
        },
        {
            "kind": "open",
            "board": "a-board",
            "date": "2026-09-22",
            "what": "a question",
            "source": "y",
        },
    ]
    progress.write_text("".join(json.dumps(r) + "\n" for r in hand), encoding="utf-8")
    monkeypatch.setattr(tp, "ROOT", tmp_path)
    monkeypatch.setattr(tp, "SCRATCH", scratch)
    monkeypatch.setattr(tp, "PROGRESS", progress)

    tp.sync()
    rows = tp.load(progress)

    board = next(r for r in rows if r["kind"] == "board")
    assert board["status"] == "in progress"
    assert board["summary"] == "written by hand"
    assert board["dates"] == "2026-09-01 to 2026-09-02"
    assert {r["ticket"]: r["status"] for r in rows if r["kind"] == "ticket"} == {
        "01": "done",
        "02": "not-started",
    }
    assert [r for r in rows if r["kind"] in ("measurement", "open")] == hand[2:]


@pytest.mark.parametrize(
    "payload", ["</script><b>x", "<!--<script>", "<script>alert(1)</script>"]
)
def test_the_page_carries_the_rows_and_cannot_be_closed_by_them(payload):
    rows = [
        {
            "kind": "note",
            "board": "b",
            "date": "2026-09-22",
            "what": payload,
            "source": "s",
        }
    ]

    page = tp.render(rows)
    script = page.split("<script>", 1)[1]

    assert (
        "<"
        not in script.split("</script>", 1)[0]
        .split("const rows = ", 1)[1]
        .split(";", 1)[0]
    )
    assert page.count("</script>") == 1


def test_a_wrapped_status_is_read_to_its_end(tmp_path, monkeypatch):
    monkeypatch.setattr(tp, "ROOT", tmp_path)
    issues = tmp_path / ".scratch" / "b" / "issues"
    issues.mkdir(parents=True)
    ticket = issues / "14-x.md"
    ticket.write_text(
        "# X\n\n```\n# not a title\n```\n\n**Status:** built (2026-09-22), and its consumer is\n"
        "**paused**.\n\nBody mentions 2027-01-01.\n",
        encoding="utf-8",
    )

    row = tp.read_ticket(ticket, "b")

    assert row["status"] == "blocked"
    assert row["title"] == "X"
    assert row["updated"] == "2026-09-22"  # the status's date, not the body's


def _board(tmp_path, monkeypatch, *, rows: list[dict]):
    scratch = tmp_path / ".scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    progress = scratch / "progress.jsonl"
    progress.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    monkeypatch.setattr(tp, "ROOT", tmp_path)
    monkeypatch.setattr(tp, "SCRATCH", scratch)
    monkeypatch.setattr(tp, "PROGRESS", progress)
    return scratch, progress


def test_a_board_with_a_spec_and_no_tickets_is_proposed_and_other_dirs_are_skipped(
    tmp_path, monkeypatch
):
    scratch, _progress = _board(tmp_path, monkeypatch, rows=[])
    (scratch / "new-board").mkdir()
    (scratch / "new-board" / "spec.md").write_text(
        "# S\n\nWhat it will do.\n", encoding="utf-8"
    )
    (scratch / "notes").mkdir()

    rows = tp.sync()

    boards = [r for r in rows if r["kind"] == "board"]
    assert [(b["board"], b["status"], b["summary"]) for b in boards] == [
        ("new-board", "proposed", "What it will do.")
    ]


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    scratch, progress = _board(
        tmp_path, monkeypatch, rows=[{"kind": "note", "board": "b", "what": "x"}]
    )
    (scratch / "b" / "issues").mkdir(parents=True)
    (scratch / "b" / "issues" / "01-a.md").write_text(
        "# A\n\n**Status:** done\n", encoding="utf-8"
    )
    before = progress.read_text(encoding="utf-8")

    tp.sync(dry_run=True)

    assert progress.read_text(encoding="utf-8") == before


def test_a_board_gone_from_scratch_is_not_dropped_with_its_summary_unless_forced(
    tmp_path, monkeypatch
):
    old = [{"kind": "board", "board": "renamed-away", "summary": "written by hand"}]
    _scratch, progress = _board(tmp_path, monkeypatch, rows=old)
    before = progress.read_text(encoding="utf-8")

    with pytest.raises(tp.Refused):
        tp.sync()
    assert progress.read_text(encoding="utf-8") == before

    tp.sync(force=True)
    assert tp.load(progress) == []
