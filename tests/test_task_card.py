"""Pin how a board card finds its task's message.

The defect, from the operator on 2026-09-18: a card took the first message
in the task's *room* from the board's loaded messages — newest first — so
tasks #2 and #3 both showed task #4's message and opened #4's flow, which
read as one task listed twice in "You handled it". A first fix matched on
`task_id` among the loaded messages; review found that fails for any task
whose message is older than the board's newest 25 (task #1's was 28th).

So the server says which message opened each task (`Task.opening`, from
`Database.opening_messages`, pinned by
`tests/test_api.py::test_a_task_carries_its_opening_message_past_the_boards_window`),
and this file keeps the screen from going back to looking it up itself.
There are no JavaScript tests here by design; the guard reads the source.
"""

from __future__ import annotations

from pathlib import Path

BOARD = (
    Path(__file__).resolve().parents[1] / "web" / "src" / "screens" / "BoardScreen.tsx"
)


def test_a_card_shows_and_opens_the_message_the_server_names() -> None:
    src = BOARD.read_text(encoding="utf-8")
    assert "const from = task.opening;" in src, (
        "TaskCard must take its message from task.opening, not look one up "
        "among the board's newest messages"
    )


def test_nothing_on_the_board_joins_a_task_to_a_message_by_room() -> None:
    src = BOARD.read_text(encoding="utf-8")
    for room_join in (
        "m.conversation === task.conversation",
        "t.conversation === m.conversation",
        "task.conversation === m.conversation",
        "m.conversation === t.conversation",
    ):
        assert room_join not in src, (
            f"BoardScreen joins a task to a message by room again ({room_join!r}) "
            "— two tasks in one room would share a message"
        )


def test_last_spoke_is_per_task() -> None:
    src = BOARD.read_text(encoding="utf-8")
    assert "newest.set(m.task_id, m.created_at)" in src, (
        "a card's 'last spoke' must come from its own task's messages"
    )
