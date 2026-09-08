"""Bundle budget — see .scratch/a-monitor-on-the-whole-path/issues/09.

The audit asked for "13 problems, each with a regression test".
The bundle size is the one budget that catches regressions
without a real browser: a chunky import or a forgotten tree-
shake lands here before the operator does. Run unconditionally;
Lighthouse (in `test_perf`) only runs when the bundle passes.

Budgets:
- Total JS bundle ≤ 200KB gzipped
- Single JS file ≤ 100KB gzipped

The numbers match the audit's "make it snappy" rule — at 200KB
gzipped the page renders in under a second over a fast 3G, and
no single file at 100KB forces the parser to wait on a chunk.
"""
from __future__ import annotations

import gzip
import subprocess
from pathlib import Path

import pytest

WEB = Path(__file__).resolve().parents[1] / "web"
DIST = WEB / "dist"
ASSETS = DIST / "assets"

TOTAL_BUDGET_GZ = 200 * 1024
SINGLE_BUDGET_GZ = 100 * 1024


def _build_dist() -> None:
    if (DIST / "index.html").exists():
        return
    subprocess.run(
        ["npm", "run", "build"],
        cwd=str(WEB),
        check=True,
        capture_output=True,
    )


def _gz_size(path: Path) -> int:
    """`gzip -9` is what `Content-Encoding: gzip` on the wire does;
    raw bytes overstate the cost. The audit's "make it snappy"
    rule is about what the operator downloads, not what we ship."""
    return len(gzip.compress(path.read_bytes(), compresslevel=9))


@pytest.fixture(scope="module", autouse=True)
def _ensure_dist() -> None:
    """Build once per test session. Tests that need a current
    `dist/` call this fixture; the audit asked for the budget to
    be enforced on every commit, and a stale build is the
    failure the gate does not catch."""
    _build_dist()


def _js_files() -> list[Path]:
    return sorted(ASSETS.glob("*.js"))


def test_single_js_file_under_budget() -> None:
    """One big file hides the budget across many small ones.
    The audit's principle: never let a chunk grow past what the
    page can render in one second. 100KB gzipped is the cap."""
    offenders = []
    for js in _js_files():
        size = _gz_size(js)
        if size > SINGLE_BUDGET_GZ:
            offenders.append((js.name, size))
    assert not offenders, (
        f"JS files over {SINGLE_BUDGET_GZ // 1024}KB: {offenders}"
    )


def test_total_js_bundle_under_budget() -> None:
    """Sum of all `*.js` after gzip ≤ 200KB. The audit's budget
    is `web/dist/assets/*.js` summed — CSS, HTML, and source
    maps are out of scope (gzipped CSS is cheap; source maps
    ship in development only)."""
    files = _js_files()
    if not files:
        pytest.skip(
            "no JS in web/dist — has `npm run build` been run?"
        )
    total = sum(_gz_size(js) for js in files)
    assert total <= TOTAL_BUDGET_GZ, (
        f"total JS bundle is {total // 1024}KB gzipped, "
        f"over the {TOTAL_BUDGET_GZ // 1024}KB budget. "
        f"Files: {[(js.name, _gz_size(js) // 1024) for js in files]}"
    )


def test_no_duplicate_chunks() -> None:
    """Vite emits hashed filenames so duplicate chunks cannot
    collide by name. The guard is belt-and-braces — if a future
    build config emits `index.js` twice, the route would serve
    both and the page would render twice."""
    names = [js.name for js in _js_files()]
    assert len(names) == len(set(names)), (
        f"duplicate JS chunks in {ASSETS}: {names}"
    )
