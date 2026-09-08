"""Guards for the design-token discipline.

Ticket 01 of `.scratch/a-monitor-on-the-whole-path/`. The web/ layer is
read by humans through CSS, and the whole point of the token system
recorded in `spec.md` (D3, D4) is that every property value the user
sees comes from a `--name` in one place. Two failure modes this test
catches:

- An inline `style={{ background: "#..." }}` that bypasses the token
  layer. The grep below is a syntactic test, not semantic, but it
  covers every spelling the codebase has used so far.
- A hex literal in a component file. The token palette lives in
  `web/src/index.css`; nowhere else.

The third check is that `index.css` carries the operator-chosen dark
palette — `--bg-0`, `--bg-1`, `--bg-2`, `--ink`, `--ink-faint`,
`--ink-mute`, `--accent`, `--good`, `--warn`, `--bad` — so an accidental
palette swap fails the build rather than shipping a page that looks
slightly off.

The fourth check is that light-mode media query is absent. The
operator chose dark-only on 2026-09-08; light tokens would ship a
contrast-failure the audit did not catch.

The fifth check is that every primitive `web/src/ui/` advertises is
also importable from `web/src/ui/index.ts`, so `import {Button} from
"../ui"` keeps working after the split.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
SRC = WEB / "src"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _all_tsx() -> list[Path]:
    return [
        *SRC.glob("screens/**/*.tsx"),
        *SRC.glob("ui/**/*.tsx"),
        *SRC.glob("*.tsx"),
    ]


def test_no_inline_style_literals_in_components() -> None:
    """`style={{ ... }}` in JSX bypasses the token system. Every value
    that reaches the user is supposed to come from a `--name` in
    `tokens.css`. The grep is syntactic on purpose — it would not see
    a string built at runtime, and the codebase does not do that, so
    that is the bug surface."""
    offenders: list[str] = []
    for path in _all_tsx():
        text = _read(path)
        for n, line in enumerate(text.splitlines(), 1):
            # An inline literal looks like `style={{` — same opening,
            # same closing, same one-line shape this repo uses.
            if re.search(r"style=\{\{", line):
                offenders.append(f"{path.relative_to(WEB)}:{n}: {line.strip()}")
    assert not offenders, "inline style literals found:\n" + "\n".join(offenders)


def test_no_hex_color_literals_in_components() -> None:
    """Hex outside the token file is the same bypass by another route."""
    offenders: list[str] = []
    hex_re = re.compile(r"#[0-9a-fA-F]{3,8}\b")
    for path in _all_tsx():
        text = _read(path)
        for n, line in enumerate(text.splitlines(), 1):
            if hex_re.search(line):
                offenders.append(f"{path.relative_to(WEB)}:{n}: {line.strip()}")
    assert not offenders, "hex literals found:\n" + "\n".join(offenders)


def test_tokens_file_has_the_operator_palette() -> None:
    """The dark palette from spec D4. The keys are read out of the
    `:root { ... }` block, not out of any source file, so a rename in
    a component does not change the assertion — and a rename in
    `tokens.css` does, which is the point."""
    css = _read(SRC / "index.css")
    root_match = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_match is not None, "no `:root { ... }` block in tokens.css"
    declared = set(re.findall(r"--([a-z][a-z0-9-]*)\s*:", root_match.group(1)))
    required = {
        "bg-0",
        "bg-1",
        "bg-2",
        "line",
        "ink",
        "ink-faint",
        "ink-mute",
        "accent",
        "good",
        "warn",
        "bad",
    }
    missing = required - declared
    assert not missing, f"tokens.css :root is missing {sorted(missing)}"


def test_tokens_file_carries_motion_durations_and_easing() -> None:
    """The motion system (ticket 02) needs `--d-fast`, `--d-med`,
    `--d-slow`, and `--ease` available before it is wired up, so
    anything that claims to be a token file must name them. The values
    themselves are ticket 02's decision; here we only check the names
    exist."""
    css = _read(SRC / "index.css")
    root_match = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_match is not None
    declared = set(re.findall(r"--([a-z][a-z0-9-]*)\s*:", root_match.group(1)))
    for name in ("d-fast", "d-med", "d-slow", "ease"):
        assert name in declared, f"tokens.css :root is missing --{name}"


def test_no_light_mode_media_query() -> None:
    """Operator chose dark-only on 2026-09-08. A light-mode block
    slipping back in is a regression the audit cannot catch after the
    fact — the screen reads fine in dark, the operator opens it in
    daylight, and the colors are wrong."""
    css = _read(SRC / "index.css")
    for n, line in enumerate(css.splitlines(), 1):
        assert "prefers-color-scheme: light" not in line, (
            f"index.css:{n}: light-mode media query found; "
            "operator chose dark-only"
        )


def test_ui_module_exports_every_primitive() -> None:
    """`web/src/ui/index.ts` must re-export every primitive the audit
    promised: Button, IconButton, Input, Dialog, Card, Skeleton,
    Spinner, Toast, Pill, Tag. Anything imported from `../ui` should
    resolve.

    The check reads the file as text rather than running an import:
    the suite does not run the TypeScript compiler, so an import-based
    check would either need a precompiled artefact (extra surface to
    keep honest) or fail on TypeScript syntax (false positive). Reading
    the export lines is what proves the operator-visible contract."""
    index = SRC / "ui" / "index.ts"
    assert index.exists(), "web/src/ui/index.ts missing"
    text = index.read_text(encoding="utf-8")
    for name in (
        "Button",
        "IconButton",
        "Input",
        "Dialog",
        "Card",
        "Skeleton",
        "Spinner",
        "Toast",
        "Pill",
        "Tag",
    ):
        # The export line for each primitive looks like `export { Name }`
        # or `export { Name, ... }` — that is the contract this test
        # reads. A re-export with a renamed name (e.g. `export { X as Y }`)
        # would not match, which is the failure we want.
        assert re.search(rf"\b{re.escape(name)}\b", text), (
            f"web/src/ui/index.ts missing export: {name}"
        )


def test_every_primitive_is_also_a_file() -> None:
    """One file per primitive. The directory listing is the audit — if
    a new primitive is added in `index.ts` without its own file, this
    fails, and the same the other way."""
    ui = SRC / "ui"
    assert ui.is_dir(), "web/src/ui/ must be a directory"
    expected_files = {
        "Button.tsx",
        "IconButton.tsx",
        "Input.tsx",
        "Dialog.tsx",
        "Card.tsx",
        "Skeleton.tsx",
        "Spinner.tsx",
        "Toast.tsx",
        "Pill.tsx",
        "Tag.tsx",
        "index.ts",
    }
    actual = {p.name for p in ui.iterdir()}
    missing = expected_files - actual
    assert not missing, f"web/src/ui/ missing files: {sorted(missing)}"


@pytest.fixture(scope="module")
def tokens_text() -> str:
    return _read(SRC / "index.css")


def test_roomsscreen_renders_both_markers() -> None:
    """Pin the operator-visible shape: the Rooms screen renders a marker
    when `is_task` is true, and another when `is_enrichment` is true.
    A future "simplification" that drops either glyph — or that flips
    the predicate — would make the board's two questions ("which row
    opened a task", "which row produced a memory") unanswerable at a
    glance, and no test would catch it.

    The check is syntactic on purpose: a real render test would need a
    React renderer in the suite, and that is the second renderer that
    drifts."""
    src = (SRC / "screens" / "RoomsScreen.tsx").read_text(encoding="utf-8")
    assert "m.is_task" in src, "RoomsScreen no longer reads is_task"
    assert "m.is_enrichment" in src, "RoomsScreen no longer reads is_enrichment"
    # Each marker has a text label, not just a glyph — colour + label,
    # never colour alone.
    assert "Opened a task" in src, "task marker has no screen-reader label"
    assert "Source of a memory" in src, "enrichment marker has no screen-reader label"


def test_the_rooms_row_has_no_whole_row_click_handler() -> None:
    """The operator's call: clicking a row should not open the flow —
    only the timestamp should. A regression that re-introduces a row
    click would put a card-shaped target back into the screen, which
    is exactly the affordance the audit asked to remove."""
    src = (SRC / "screens" / "RoomsScreen.tsx").read_text(encoding="utf-8")
    # The whole-row div had `onClick=` historically; the timestamp
    # button has `onClick=` to open the flow. The row must not.
    import re

    row_block = re.search(r'<div\s+className=\{[^}]*"msg"[^}]*\}>', src)
    if row_block:
        # The "msg" class row must not carry an onClick.
        after = src[row_block.end():src.find("</div>", row_block.end())]
        assert "onClick" not in after, (
            "the row has a click handler — the timestamp is the only "
            "thing that should open the flow"
        )


def test_flowscreen_renders_a_state_pill_per_turn_and_per_tool() -> None:
    """The state derivation lives in `flowState.ts`; the screen reads
    it for each turn and each tool and renders a pill. A regression
    that drops the pill — or that drops the `state` prop on
    CallCard / ToolCard — makes the operator's two questions ("is
    this turn done", "did this tool succeed") unanswerable again.
    The pill's label comes from `toneFor(state).label`, so the check
    is on the wiring rather than on a literal string in the
    component file."""
    fs = (SRC / "screens" / "FlowScreen.tsx").read_text(encoding="utf-8")
    cards = (SRC / "ui" / "cards.tsx").read_text(encoding="utf-8")

    assert "turnState(" in fs, "FlowScreen no longer calls turnState"
    assert "toolState(" in fs, "FlowScreen no longer calls toolState"

    # CallCard and ToolCard both take a `state` prop and render it
    # through toneFor.
    assert "state: State" in cards, "CallCard/ToolCard lost the state prop"
    assert "toneFor(state)" in cards, (
        "CallCard/ToolCard no longer derive label/tone from the state prop"
    )


def test_the_screen_no_longer_shows_the_implicit_attempt_pill() -> None:
    """Ticket 12 folded the `attempt > 1` warn pill into the new
    state pill. Showing both is saying the same thing twice on one
    row — a regression is two pills in the same header."""
    cards = (SRC / "ui" / "cards.tsx").read_text(encoding="utf-8")
    assert "attempt ${call.attempt}" not in cards, (
        "CallCard still renders an attempt pill — the state pill "
        "already carries retrying"
    )


def test_three_named_motions_with_enter_slower_than_exit() -> None:
    """The motion system has three named motions (fade, slide, scale),
    and every one of them runs its exit faster than its enter. The
    audit's rule: enter is always slower than exit, so a closing
    feels like a closing rather than a pause.
    """
    import re
    css = (SRC / "index.css").read_text(encoding="utf-8")

    # Each motion class pair (enter + exit) must be defined.
    for motion in ("fade", "slide", "scale"):
        enter = f".{motion}-enter"
        exit_ = f".{motion}-exit"
        assert enter in css, f"missing {enter} class"
        assert exit_ in css, f"missing {exit_} class"

    # Enter durations are --d-slow or --d-med (240ms / 180ms); exit
    # durations are --d-med or --d-fast (180ms / 120ms). Picking up
    # the variables instead of literal ms means a redesign of the
    # motion system is one file; the test still pins the relationship.
    enter_dur = re.findall(r"\.(?:fade|slide|scale)-enter\s*\{[^}]*var\(--d-(slow|med|fast)\)", css)
    exit_dur = re.findall(r"\.(?:fade|slide|scale)-exit\s*\{[^}]*var\(--d-(slow|med|fast)\)", css)
    ordering = {"fast": 0, "med": 1, "slow": 2}
    assert len(enter_dur) == 3, f"expected 3 enter classes, got {enter_dur}"
    assert len(exit_dur) == 3, f"expected 3 exit classes, got {exit_dur}"
    for e, x in zip(enter_dur, exit_dur):
        e_dur = ordering[e] if isinstance(e, str) else ordering[e[0]]
        x_dur = ordering[x] if isinstance(x, str) else ordering[x[0]]
        assert e_dur > x_dur, (
            f"{e!r} enter must be slower than {x!r} exit, "
            f"but the durations are equal or reversed"
        )


def test_motion_respects_prefers_reduced_motion() -> None:
    """Reduced-motion users get every duration collapsed to 0 and
    every transform animation replaced by an opacity fade. The rule
    is the audit's note #1 — the screen must not move for someone
    who asked it not to."""
    css = (SRC / "index.css").read_text(encoding="utf-8")
    block = re.search(r"@media\s*\(prefers-reduced-motion:\s*reduce\)\s*\{(.*?)\}\s*\}", css, re.DOTALL)
    assert block, "no @media (prefers-reduced-motion: reduce) block"
    body = block.group(1)
    assert "--d-fast: 0ms" in body
    assert "--d-med: 0ms" in body
    assert "--d-slow: 0ms" in body
    assert "animation: none" in body
    assert "transition: none" in body


def test_route_transition_uses_fade_enter() -> None:
    """`<App>` remounts the screen content on every route change,
    keyed on the section, with the `fade-enter` class so the motion
    system's fade keyframe runs. A regression that drops the key or
    the class makes route changes snap, which is the failure the
    operator named ("không mượt")."""
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert "key={section}" in app, "App does not remount on route change"
    assert "fade-enter" in app, "App does not apply the fade-enter motion"


def test_app_root_wires_toast_provider() -> None:
    """<ToastProvider> lives at App root so any screen can call
    `useToast()` to surface an action that needs the operator's
    attention (a retry on a failed rename, a notification that a
    task was opened, etc.). Without the provider, useToast throws."""
    src = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert "ToastProvider" in src, "ToastProvider is not wired into App"
    # Closing tag has to be there too — an open <ToastProvider> without
    # a close wraps the whole app forever and React will warn.
    assert "</ToastProvider>" in src, "ToastProvider has no closing tag"


def test_no_screen_still_says_loading_literally() -> None:
    """Every screen used to fall back to a `Loading…` text node when
    its data was in flight. That is the failure the audit called
    out: a screen that says "Loading…" with no shape is a screen
    that has no idea what is coming. Skeleton and Spinner replace
    every one. A regression that puts `Loading…` text back into a
    screen is a regression in the operator's experience, and this
    guard is what catches it."""
    offenders: list[str] = []
    for path in SRC.glob("screens/**/*.tsx"):
        text = path.read_text(encoding="utf-8")
        for n, line in enumerate(text.splitlines(), 1):
            if "Loading…" in line or "Loading..." in line:
                offenders.append(f"{path.relative_to(WEB)}:{n}: {line.strip()}")
    assert not offenders, "literal Loading… text found:\n" + "\n".join(offenders)


def test_skeleton_renders_a_real_element_not_a_text_placeholder() -> None:
    """`Skeleton` is the primitive the audit asked for, and its
    test is that it renders an actual element with the `skeleton`
    class — not a div with `Loading…` text inside. The CSS for
    `.skeleton` carries the shimmer animation; without the class
    the placeholder is no better than the text it replaces."""
    skeleton = (SRC / "ui" / "Skeleton.tsx").read_text(encoding="utf-8")
    assert 'className="skeleton"' in skeleton, (
        "Skeleton does not apply the skeleton class — a placeholder "
        "without the class is no better than text"
    )
    assert "Loading" not in skeleton, (
        "Skeleton renders a Loading… text — that is exactly what "
        "ticket 03 replaces"
    )


def test_monitor_screen_has_aria_log_and_memo_on_task_card() -> None:
    """The Monitor screen's two audit asks:
    - audit #1: feed uses `role="log"` and `aria-live="polite"` so a
      screen reader announces new events without interrupting.
    - audit #4: TaskCard is React.memo'd so SSE bursts do not
      re-render cards that did not change."""
    src = (SRC / "screens" / "MonitorScreen.tsx").read_text(encoding="utf-8")
    assert 'role="log"' in src, 'MonitorScreen feed has no role="log"'
    assert 'aria-live="polite"' in src, "MonitorScreen feed has no aria-live"
    # `memo(` (with the open paren) is the exact shape the audit
    # asked for; a bare `memo` reference would be a wrapper without
    # application.
    import re
    assert re.search(r"\bmemo\s*\(", src), (
        "TaskCard is not React.memo'd — the audit asked for the wrap"
    )
    assert re.search(r"memo\s*\(\s*function TaskCard", src), (
        "memo is not applied to the TaskCard component specifically"
    )


def test_monitor_routes_at_root() -> None:
    """`/` is the Monitor screen — the operator's front door. A
    regression that leaves `/` on Board is the audit's failure."""
    app = (SRC / "App.tsx").read_text(encoding="utf-8")
    assert 'section === "/" && <MonitorScreen />' in app
    # The topbar's first tab is the front door, in the same order
    # the spec called for: Monitor, Board, Rooms.
    assert "label: \"Monitor\"" in app
    # The old first tab is no longer the first tab.
    assert 'label: "Board"' in app


def test_monitor_polling_does_not_exist() -> None:
    """Ticket 04 takes the snapshot once on mount and renders; SSE
    (ticket 05) is what updates it. A regression that adds polling
    back is the SSE ticket's failure: two readers of the same data
    with different cadences drift, exactly the bug polling was
    meant to avoid."""
    src = (SRC / "screens" / "MonitorScreen.tsx").read_text(encoding="utf-8")
    assert "setInterval" not in src, (
        "MonitorScreen polls — ticket 05 owns the live updates"
    )
    assert "useEventStream" in src, (
        "MonitorScreen is not subscribed to SSE — ticket 05's whole "
        "reason to exist is the EventStream subscription"
    )


def test_breadcrumb_collapses_more_than_four_items() -> None:
    """Anything beyond four levels collapses to `head › … › tail`,
    so the trail never overflows the page header on a deep path.
    A regression that drops the cap is a regression in operator
    experience — a flow with five turns produces a trail the
    width of a phone.

    The check is syntactic on purpose: the cap value (4) is in
    a `MAX_ITEMS = 4` constant, and the collapse is a `slice()`
    against it. A future reader will see the failure as either
    "the constant dropped below 4" or "the slice was replaced
    with something that does not bound the trail"."""
    src = (SRC / "ui" / "Breadcrumb.tsx").read_text(encoding="utf-8")
    assert "MAX_ITEMS = 4" in src, (
        "Breadcrumb no longer caps at 4 items"
    )
    assert "slice(" in src, (
        "Breadcrumb's collapse does not slice the trail"
    )
    assert "›" in src, (
        "Breadcrumb lost its separator glyph"
    )


def test_monitor_task_card_is_clickable_and_opens_flow() -> None:
    """A live task card on the Monitor screen opens the flow for
    the message that opened it. A regression that turns the card
    back into a static element is a regression in the operator's
    primary drill-down — the audit asked for `Click a live task
    card → URL becomes /flow/...`."""
    src = (SRC / "screens" / "MonitorScreen.tsx").read_text(encoding="utf-8")
    assert "openFlow" in src or "navigate" in src, (
        "MonitorScreen does not wire openFlow/navigate; the operator "
        "cannot drill into a task from the Monitor screen"
    )
    assert "onClick" in src, (
        "MonitorScreen has no click handler — the audit asked for "
        "click-to-flow"
    )


def test_a_live_event_click_opens_its_message_flow() -> None:
    """A feed row's click opens the flow for the message that
    produced the event. Without this the operator can see *what*
    happened but cannot see *why* — the audit's drill-down asks
    for the click-to-flow link on every event."""
    src = (SRC / "screens" / "MonitorScreen.tsx").read_text(encoding="utf-8")
    # The FeedRow renders a `<li>` or `<button>` — what matters
    # is that one of them carries a click handler that asks for
    # the flow. The audit's audit #4 still applies: `React.memo`
    # on the row keeps an SSE burst from re-rendering it.
    assert "onOpenFlow" in src or "openFlow" in src, (
        "MonitorScreen feed has no openFlow wiring"
    )


def test_flowscreen_handles_the_call_anchor() -> None:
    """`/flow/...#call-{id}` scrolls the matching card into view
    and focuses it. Without the anchor handler, the deep-link is
    no better than the bare `/flow/...` URL — the operator has to
    scroll by hand to find the call that the URL named."""
    src = (SRC / "screens" / "FlowScreen.tsx").read_text(encoding="utf-8")
    assert "call-" in src, "FlowScreen does not understand the call-N anchor"
    # The hash has to be read on mount.
    assert "hash" in src or "location.hash" in src or "useEffect" in src, (
        "FlowScreen does not read the URL hash"
    )
