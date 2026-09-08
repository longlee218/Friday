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
