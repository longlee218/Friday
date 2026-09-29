"""Pin the Flow screen's per-step state derivation.

The state rules live in `web/src/flowState.ts`. They are pure data,
so this test mirrors them in Python and asserts the contract a future
edit has to keep — same shapes, same rules, same order. The mirror is
checked against the TS source at the end of the file, so a TypeScript
edit that does not land the corresponding line here is a regression
in its own right.

Both copies exist because the only way to test the TypeScript file
from this Python suite is either to spin up a JS runtime here, which
adds a second renderer and a class of bugs we already paid for, or to
mirror the rules in Python. The mirror is small and the rules are
pure; it is the cheaper of the two by a margin.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True)
class ModelCall:
    id: int = 100
    attempt: int = 1
    input_tokens: int = 100
    output_tokens: int = 50


@dataclass(frozen=True)
class ToolCall:
    failed: bool = False
    created_at: datetime = datetime(2026, 9, 7, 12, 0, tzinfo=UTC)


@dataclass(frozen=True)
class Outbound:
    # Default id 50 — *before* the default call id (100) — so an
    # `Outbound()` with no overrides represents an outbound that
    # happened *before* the call, which is the case almost every test
    # here cares about. Tests that want "this turn produced the
    # outbound" pass an explicit id > 100.
    id: int = 50
    kind: str = "reply"


@dataclass(frozen=True)
class Flow:
    outbound: tuple[Outbound, ...] = ()


# Python mirror of web/src/flowState.ts. The order is load-bearing —
# earlier rules beat later ones on ambiguous inputs, the same as the
# TS source.


def tool_state(call: ToolCall) -> str:
    return "failed" if call.failed else "ok"


def turn_state(call: ModelCall, tools: list[ToolCall], flow: Flow) -> str:
    if any_tool_failed(tools) or call_has_error(call):
        return "failed"
    if call.attempt > 1:
        return "retrying"
    if turn_ended_waiting(call, flow):
        return "waiting"
    return "done"


def any_tool_failed(tools: list[ToolCall]) -> bool:
    return any(t.failed for t in tools)


def call_has_error(call: ModelCall) -> bool:
    # `unanswered` is the wire signal: zero tokens in, zero tokens out.
    return call.input_tokens == 0 and call.output_tokens == 0


def turn_ended_waiting(call: ModelCall, flow: Flow) -> bool:
    if not flow.outbound:
        return False
    later = [r for r in flow.outbound if r.id > call.id]
    if not later:
        return False
    last = later[-1]
    return last.kind in {"ask_for_details", "approval_card"}


# --- tests --------------------------------------------------------------


def test_tool_state_ok_when_not_failed() -> None:
    assert tool_state(ToolCall(failed=False)) == "ok"


def test_tool_state_failed_when_failed() -> None:
    assert tool_state(ToolCall(failed=True)) == "failed"


def test_turn_state_done_when_nothing_went_wrong() -> None:
    assert (
        turn_state(
            ModelCall(),
            [ToolCall()],
            Flow(),
        )
        == "done"
    )


def test_turn_state_failed_when_a_tool_failed() -> None:
    assert (
        turn_state(
            ModelCall(),
            [ToolCall(failed=True)],
            Flow(),
        )
        == "failed"
    )


def test_turn_state_failed_when_call_was_unanswered() -> None:
    assert (
        turn_state(
            ModelCall(input_tokens=0, output_tokens=0),
            [ToolCall()],
            Flow(),
        )
        == "failed"
    )


def test_turn_state_retrying_when_call_was_retried() -> None:
    """`attempt > 1` is the cheapest sign a provider was struggling.
    Failure beats retry in the rule order — a turn that both retried
    and has a failed tool is `failed`, not `retrying`."""
    assert (
        turn_state(
            ModelCall(attempt=2),
            [ToolCall()],
            Flow(),
        )
        == "retrying"
    )


def test_turn_state_failed_beats_retrying() -> None:
    assert (
        turn_state(
            ModelCall(attempt=2),
            [ToolCall(failed=True)],
            Flow(),
        )
        == "failed"
    )


def test_turn_state_waiting_when_outbound_asked_for_details() -> None:
    """The agent asked the reporter for a field it could not lift."""
    assert (
        turn_state(
            ModelCall(),
            [ToolCall()],
            Flow(outbound=(Outbound(id=200, kind="ask_for_details"),)),
        )
        == "waiting"
    )


def test_turn_state_waiting_when_outbound_is_approval_card() -> None:
    """The agent drafted a reply and is waiting for the operator's OK."""
    assert (
        turn_state(
            ModelCall(),
            [ToolCall()],
            Flow(outbound=(Outbound(id=200, kind="approval_card"),)),
        )
        == "waiting"
    )


def test_turn_state_done_for_ordinary_reply_outbound() -> None:
    """A `reply` outbound that has already been sent does not make a
    turn `waiting` — the conversation moved on."""
    assert (
        turn_state(
            ModelCall(),
            [ToolCall()],
            Flow(outbound=(Outbound(id=200, kind="reply"),)),
        )
        == "done"
    )


def test_turn_state_done_when_outbound_is_before_the_call() -> None:
    """An outbound that was queued before this turn's call belongs to
    an earlier turn — it does not make *this* one `waiting`. The
    default outbound id (50) is smaller than the default call id
    (100), so a plain `Outbound()` already represents "before"."""
    assert (
        turn_state(
            ModelCall(),
            [ToolCall()],
            Flow(
                outbound=(Outbound(kind="ask_for_details"),),
            ),
        )
        == "done"
    )


def test_turn_state_retrying_beats_waiting() -> None:
    """A turn that retried and then handed off to the operator is
    `retrying` — the operator asked for that signal more loudly than
    they asked for `waiting`."""
    assert (
        turn_state(
            ModelCall(attempt=2),
            [ToolCall()],
            Flow(outbound=(Outbound(id=200, kind="approval_card"),)),
        )
        == "retrying"
    )


def test_the_ts_source_matches_this_mirror() -> None:
    """The TS source carries the same rule words in the same order.
    A change in one that does not land in the other is a regression
    in its own right."""
    src = (
        Path(__file__).resolve().parents[1] / "web" / "src" / "flowState.ts"
    ).read_text()

    # The TS function names must exist with the right names.
    assert "export function toolState" in src
    assert "export function turnState" in src

    # The four turn-state values appear in the rule order inside
    # `turnState` (failed → retrying → waiting → done). The docstring
    # and the type alias list them in a different order, so the check
    # is anchored on the function body — a slice starting at the
    # function declaration and ending at the function's closing brace.
    body_start = src.find("export function turnState(")
    assert body_start > 0
    body = src[body_start:]
    # Find the matching closing brace by depth.
    depth = 0
    body_end = 0
    for i, ch in enumerate(body):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                body_end = i
                break
    body = body[:body_end]

    states = ["failed", "retrying", "waiting", "done"]
    last = -1
    for s in states:
        idx = body.find(f'"{s}"')
        assert idx > last, (
            f"TS source's turnState does not carry {s!r} in the order "
            f"failed, retrying, waiting, done — body slice:\n{body}"
        )
        last = idx

    # All four turn-state values must appear in TURN_STATES so a
    # ToneFor lookup cannot return `undefined` for a state the rules
    # just produced. The shape is `name: { label: "name", tone: ... }`
    # — checking the literal `"name":` form is what catches a dropped
    # row, since `"done"` would otherwise appear only inside
    # `turnState` and the screen would render `undefined` for the
    # done state.
    for s in states:
        assert f'{s}: {{ label: "{s}"' in src, (
            f"TURN_STATES does not carry an entry for {s!r}"
        )

    # The two outbound kinds that mean "waiting" appear as a literal set.
    assert '"ask_for_details"' in src
    assert '"approval_card"' in src

    # Same for the tool states.
    assert '"ok"' in src
    assert '"failed"' in src
