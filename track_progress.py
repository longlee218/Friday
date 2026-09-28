"""Keep `.scratch/progress.jsonl` in step with the boards, and draw it.

    uv run track_progress.py              # sync, then write data/progress.html
    uv run track_progress.py sync --dry-run
    uv run track_progress.py html --open

**The ticket file is the source of truth; the row points at it.** `sync`
rebuilds every `board` and `ticket` row from `.scratch/<board>/` — the
ticket's H1 and its `**Status:**` line — and keeps every other row as it was:
measurements, evals, milestones, notes and open items are written by hand and
no file can regenerate them. A board's hand-written `summary` and `dates`
survive a sync too; a board seen for the first time gets its spec's first
paragraph.

A status line is free prose ("done except the `[db]` step…"), so it is read
by rule into a small set, and a line no rule recognises is `unknown` rather
than a guess — `sync` prints it, and the fix is a clearer status line in the
ticket, not a smarter rule here.

Standard library only: this is a script for the operator's terminal, not part
of Friday, and it should run without the service's dependencies.
"""

from __future__ import annotations

import argparse
import html
import json
import re
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRATCH = ROOT / ".scratch"
PROGRESS = SCRATCH / "progress.jsonl"
HTML_OUT = ROOT / "data" / "progress.html"

#: Boards whose spec lives outside the board directory.
SPEC_ELSEWHERE = {"discord-mention-triage": "docs/SPEC.md"}

#: Rows `sync` owns. Every other kind is hand-written and kept verbatim.
DERIVED = {"board", "ticket"}

STATUSES = ("done", "part-done", "not-started", "blocked", "withdrawn", "proposed", "unknown")

#: First match wins, so the order is the precedence: "done except X" must be
#: caught as partial before "done" catches it as finished.
_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    # Anchored: "done. Two of the bullets below are superseded" is done.
    ("withdrawn", re.compile(r"^\W*(superseded|retired|withdrawn|wontfix|won't fix)\b")),
    ("not-started", re.compile(r"(?<!done; )\bnot started\b|\bready-for-agent\b")),
    # "no longer blocked" and "precondition met" are not blocked.
    ("blocked", re.compile(
        r"(?<!no longer )\bblocked\b|\bprecondition\b(?! met)|\bready-for-human\b"
        r"|\bneeds-info\b|\bwaiting on\b|\bpaused\b"
    )),
    ("part-done", re.compile(r"\bpart(ly|ially)? done\b|\bhalf\b|\bexcept\b|\bowed\b|\bin progress\b|\bpartial")),
    ("proposed", re.compile(r"\bproposed\b|\bneeds-triage\b")),
    ("done", re.compile(r"\bdone\b|\banswered\b|\bcomplete[d]?\b|\blanded\b|\bbuilt\b")),
)

#: The status runs to the first blank line: a wrapped status keeps its end
#: ("its consumer is\npaused").
_STATUS_LINE = re.compile(r"^\*\*Status:?\*\*:?[ \t]*((?:.+\n?)+)", re.M)
_FENCE = re.compile(r"^```.*?^```", re.M | re.S)
_H1 = re.compile(r"^#\s+(.+)$", re.M)
_DATE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_LABEL = re.compile(r"\b(needs-triage|needs-info|ready-for-agent|ready-for-human|wontfix)\b")


def classify(status_line: str | None) -> str:
    """One of `STATUSES`, read from a ticket's status prose."""
    if not status_line:
        return "unknown"
    text = re.sub(r"[*_`]", "", status_line).lower()
    for status, pattern in _RULES:
        if pattern.search(text):
            return status
    return "unknown"


def read_ticket(path: Path, board: str) -> dict:
    text = path.read_text(encoding="utf-8")
    prose = _FENCE.sub("", text)  # a `# comment` in a code block is not a title
    status = _STATUS_LINE.search(prose)
    status_line = re.sub(r"\s+", " ", status.group(1)).strip() if status else None
    title = _H1.search(prose)
    head = "\n".join(prose.splitlines()[:25])
    label = _LABEL.search(head)
    dates = _DATE.findall(status_line or "")  # the status's own date, never the body's
    return {
        "kind": "ticket",
        "board": board,
        "ticket": path.name.split("-", 1)[0],
        "title": title.group(1).strip() if title else path.stem,
        "status": classify(status_line),
        "label": label.group(1) if label else None,
        "status_line": (status_line or "(no status line)")[:200],
        "updated": max(dates) if dates else None,
        "path": str(path.relative_to(ROOT)),
    }


def find_spec(board_dir: Path) -> Path | None:
    elsewhere = SPEC_ELSEWHERE.get(board_dir.name)
    if elsewhere and (ROOT / elsewhere).exists():
        return ROOT / elsewhere
    for name in ("spec.md", "SPEC.md", "PRD.md", "prd.md"):
        if (board_dir / name).exists():
            return board_dir / name
    return None


def first_paragraph(spec: Path | None, limit: int = 600) -> str:
    """The spec's first prose paragraph, for a board seen the first time."""
    if spec is None:
        return ""
    for block in spec.read_text(encoding="utf-8").split("\n\n"):
        block = block.strip()
        if block and not block.startswith(("#", "Status", "---", "|", "```")):
            return re.sub(r"\s+", " ", block)[:limit]
    return ""


def board_status(tickets: list[dict]) -> str:
    if not tickets:
        return "proposed"
    if all(t["status"] in ("done", "withdrawn") for t in tickets):
        return "done"
    return "in progress"


def scan(existing: dict[str, dict]) -> list[dict]:
    """Board and ticket rows for every board under `.scratch/`."""
    rows: list[dict] = []
    for board_dir in sorted(p for p in SCRATCH.iterdir() if p.is_dir()):
        issues = board_dir / "issues"
        tickets = (
            [read_ticket(p, board_dir.name) for p in sorted(issues.glob("*.md"))]
            if issues.is_dir()
            else []
        )
        spec = find_spec(board_dir)
        if spec is None and not tickets:
            continue  # not a board: some other directory under .scratch
        old = existing.get(board_dir.name, {})
        updated = sorted(t["updated"] for t in tickets if t["updated"])
        rows.append({
            "kind": "board",
            "board": board_dir.name,
            "spec": str(spec.relative_to(ROOT)) if spec else None,
            "status": board_status(tickets),
            "dates": old.get("dates") or (f"{updated[0]} to {updated[-1]}" if updated else None),
            "summary": old.get("summary") or first_paragraph(spec),
        })
        rows.extend(tickets)
    return rows


def load(path: Path | None = None) -> list[dict]:
    path = path or PROGRESS  # read at call time, not bound at import
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def merge(old: list[dict], derived: list[dict]) -> list[dict]:
    """Derived rows first, in board order; then every hand-written row, as it was."""
    kept = [r for r in old if r.get("kind") not in DERIVED]
    return derived + kept


def _key(r: dict) -> tuple:
    return (r.get("kind"), r.get("board"), r.get("ticket"))


def duplicates(rows: list[dict]) -> list[tuple]:
    seen = Counter(_key(r) for r in rows if r.get("kind") in DERIVED)
    return [k for k, n in seen.items() if n > 1]


def changes(old: list[dict], new: list[dict]) -> list[str]:
    key = _key

    before = {key(r): r for r in old if r.get("kind") in DERIVED}
    after = {key(r): r for r in new if r.get("kind") in DERIVED}
    out = []
    for k in sorted(after.keys() - before.keys()):
        out.append(f"+ {k[0]} {k[1]} {k[2] or ''}".rstrip())
    for k in sorted(before.keys() - after.keys()):
        out.append(f"- {k[0]} {k[1]} {k[2] or ''}".rstrip())
    for k in sorted(before.keys() & after.keys()):
        if before[k].get("status") != after[k].get("status"):
            out.append(f"~ {k[1]} {k[2] or '(board)'}: {before[k].get('status')} → {after[k].get('status')}")
    return out


class Refused(Exception):
    """A sync would throw away something written by hand."""


def sync(dry_run: bool = False, force: bool = False) -> list[dict]:
    old = load()
    existing = {r["board"]: r for r in old if r.get("kind") == "board" and r.get("board")}
    new = merge(old, scan(existing))
    for line in changes(old, new) or ["no status changes"]:
        print(line)
    for k in duplicates(new):
        print(f"! duplicate {k[0]} {k[1]} {k[2] or ''}: two files share a number")
    kept_boards = {r["board"] for r in new if r.get("kind") == "board"}
    lost = [b for b, r in existing.items() if b not in kept_boards and r.get("summary")]
    if lost and not force and not dry_run:
        # A renamed or deleted board takes its hand-written summary with it,
        # and nothing can regenerate that. Say so; write nothing.
        raise Refused(
            f"board(s) {', '.join(sorted(lost))} are gone from .scratch but have a hand-written "
            "summary in progress.jsonl; rename them there, or rerun with --force to drop them"
        )
    unknown = [r for r in new if r.get("kind") == "ticket" and r["status"] == "unknown"]
    for r in unknown:
        print(f"? {r['path']}: status not recognised — {r['status_line'][:80]}")
    tally = Counter(r["status"] for r in new if r.get("kind") == "ticket")
    print("tickets:", ", ".join(f"{s} {tally[s]}" for s in STATUSES if tally[s]))
    if not dry_run:
        PROGRESS.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in new), encoding="utf-8"
        )
        print(f"wrote {PROGRESS.relative_to(ROOT)} ({len(new)} rows)")
    return new


# ── timing from git (derived at render, never persisted) ─────────────────────

_GIT_OK: bool | None = None


def _mtime_iso(path: Path) -> str | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    except OSError:
        return None


def _git_log_dates(rel_path: str) -> list[str]:
    """Committer dates (ISO, newest first) of every commit that touched a path."""
    global _GIT_OK
    if _GIT_OK is False:
        return []
    try:
        r = subprocess.run(
            ["git", "log", "--format=%cI", "--", rel_path],
            cwd=ROOT, capture_output=True, text=True, timeout=15,
        )
    except (OSError, subprocess.SubprocessError):
        _GIT_OK = False
        return []
    if r.returncode != 0:
        _GIT_OK = False
        return []
    _GIT_OK = True
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def timing(path: Path) -> tuple[str | None, str | None]:
    """`(created, touched)` for a ticket: first git-add / last commit, else mtime.

    A file not yet committed (or a tree with no git) falls back to the file's
    modification time for both, so a brand-new ticket still places on the
    timeline instead of vanishing.
    """
    dates = _git_log_dates(str(path.relative_to(ROOT))) if path.exists() else []
    if dates:
        return dates[-1], dates[0]  # oldest commit = created, newest = touched
    m = _mtime_iso(path)
    return m, m


def enrich(rows: list[dict]) -> list[dict]:
    """Add `created`/`touched` to ticket rows for the timeline; not persisted."""
    out = []
    for r in rows:
        if r.get("kind") == "ticket" and r.get("path"):
            created, touched = timing(ROOT / r["path"])
            r = {**r, "created": created, "touched": touched}
        out.append(r)
    return out


# ── the page ────────────────────────────────────────────────────────────────

def render(rows: list[dict]) -> str:
    """One self-contained page: the rows embedded, no network, no build step."""
    # Every "<" escaped, not only "</": "<!--<script>" in a status line would
    # otherwise swallow the real </script> and blank the page.
    data = json.dumps(enrich(rows), ensure_ascii=False).replace("<", "\\u003c")
    stamp = html.escape(date.today().isoformat())
    return _PAGE.replace("__DATA__", data).replace("__STAMP__", stamp)


def write_html(rows: list[dict], open_it: bool = False) -> Path:
    HTML_OUT.parent.mkdir(parents=True, exist_ok=True)
    HTML_OUT.write_text(render(rows), encoding="utf-8")
    print(f"wrote {HTML_OUT.relative_to(ROOT)}")
    if open_it:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        subprocess.run([opener, str(HTML_OUT)], check=False)
    return HTML_OUT


_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Friday progress</title>
<style>
:root{
  color-scheme:light dark;
  --bg:#f6f7f9;--panel:#fff;--panel2:#f1f3f7;--ink:#151c29;--mute:#5a6473;--faint:#8b94a3;
  --line:#e5e9f0;--cardline:#e7ebf2;--hover:#eef1f6;
  --shadow:0 1px 2px rgba(16,24,40,.04),0 2px 8px rgba(16,24,40,.06);
  --accent:#16a34a;--ring:#2563eb;
  --done:#16a34a;--partial:#c07d00;--todo:#64748b;--blocked:#dc2626;--dropped:#98a1af;--proposed:#5257e6;--unknown:#7c3aed;
  --sp2:8px;--sp3:12px;--sp4:16px;--sp5:24px;--r:10px;
  --sans:"Inter",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:ui-monospace,"JetBrains Mono","SF Mono",Menlo,Consolas,monospace;
}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){
  --bg:#0f172a;--panel:#1b2336;--panel2:#172033;--ink:#e8edf7;--mute:#94a3b8;--faint:#5d6b85;
  --line:#28324b;--cardline:#2c3752;--hover:#212c46;
  --shadow:0 1px 0 rgba(0,0,0,.25);--accent:#22c55e;--ring:#60a5fa;
  --done:#22c55e;--partial:#eab308;--todo:#94a3b8;--blocked:#ef4444;--dropped:#475569;--proposed:#818cf8;--unknown:#a78bfa;
}}
:root[data-theme=dark]{
  --bg:#0f172a;--panel:#1b2336;--panel2:#172033;--ink:#e8edf7;--mute:#94a3b8;--faint:#5d6b85;
  --line:#28324b;--cardline:#2c3752;--hover:#212c46;
  --shadow:0 1px 0 rgba(0,0,0,.25);--accent:#22c55e;--ring:#60a5fa;
  --done:#22c55e;--partial:#eab308;--todo:#94a3b8;--blocked:#ef4444;--dropped:#475569;--proposed:#818cf8;--unknown:#a78bfa;
}
*{box-sizing:border-box}
html{scrollbar-gutter:stable both-edges}
body{margin:0;background:var(--bg);color:var(--ink);font:13px/1.45 var(--sans);-webkit-font-smoothing:antialiased}
.mono{font-family:var(--mono);font-variant-numeric:tabular-nums}
.mute{color:var(--mute)}.faint{color:var(--faint)}
main{max-width:1280px;margin:0 auto;padding:0 var(--sp5) 64px}
button{font:inherit;cursor:pointer}
:focus-visible{outline:2px solid var(--ring);outline-offset:2px;border-radius:6px}
header{position:sticky;top:0;z-index:5;background:color-mix(in srgb,var(--bg) 88%,transparent);backdrop-filter:saturate(1.4) blur(8px);border-bottom:1px solid var(--line);padding:14px var(--sp5) 12px;margin:0 calc(-1*var(--sp5))}
.hd{max-width:1280px;margin:0 auto;padding:0 var(--sp5)}
.top{display:flex;align-items:center;justify-content:space-between;gap:var(--sp4);flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.dot{width:9px;height:9px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px color-mix(in srgb,var(--accent) 22%,transparent)}
h1{font-size:15px;font-weight:650;margin:0;letter-spacing:-.01em}
.sync{font-size:11.5px;color:var(--faint)}
.stats{display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.stat{display:flex;flex-direction:column;line-height:1.15}
.stat b{font-size:16px;font-weight:650;font-family:var(--mono);font-variant-numeric:tabular-nums}
.stat span{font-size:10.5px;letter-spacing:.04em;text-transform:uppercase;color:var(--faint)}
.ring{--p:0;width:38px;height:38px;border-radius:50%;background:conic-gradient(var(--done) calc(var(--p)*1%),var(--line) 0);display:grid;place-items:center}
.ring i{width:28px;height:28px;border-radius:50%;background:var(--bg);display:grid;place-items:center;font:600 10px/1 var(--mono);font-variant-numeric:tabular-nums}
.tools{display:flex;gap:var(--sp2);flex-wrap:wrap;align-items:center;margin-top:12px}
.search{position:relative;flex:1;min-width:200px}
.search svg{position:absolute;left:9px;top:50%;transform:translateY(-50%);color:var(--faint);pointer-events:none}
.search input{width:100%;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 10px 7px 30px;font:inherit;box-shadow:var(--shadow)}
select{background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:8px;padding:7px 9px;font:inherit;box-shadow:var(--shadow)}
.pill{border:1px solid var(--line);background:var(--panel);color:var(--mute);border-radius:999px;padding:5px 11px;font-size:12px;display:inline-flex;align-items:center;gap:6px;transition:border-color .15s,color .15s}
.pill .sw{width:7px;height:7px;border-radius:50%}
.pill[aria-pressed=true]{color:var(--ink);border-color:color-mix(in srgb,var(--ink) 45%,var(--line))}
.seg{display:inline-flex;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:2px}
.seg button{border:0;background:none;color:var(--mute);border-radius:6px;padding:5px 11px;font-size:12px;font-weight:550}
.seg button[aria-pressed=true]{background:var(--panel);color:var(--ink);box-shadow:var(--shadow)}
.icon{border:1px solid var(--line);background:var(--panel);color:var(--mute);border-radius:8px;width:32px;height:32px;display:grid;place-items:center;box-shadow:var(--shadow)}
.link{background:none;border:0;color:var(--accent);cursor:pointer;font:inherit;font-size:12px;padding:0}
.attn{display:flex;gap:var(--sp2);flex-wrap:wrap;margin-top:14px}
.attn .a{display:flex;align-items:center;gap:8px;background:color-mix(in srgb,var(--blocked) 10%,var(--panel));border:1px solid color-mix(in srgb,var(--blocked) 30%,var(--line));border-radius:8px;padding:6px 10px;font-size:12px}
.attn .a b{font-weight:600}
/* board / kanban */
.board-row{display:flex;gap:var(--sp3);overflow-x:auto;padding:20px 0 8px;scroll-snap-type:x proximity}
.col{flex:0 0 300px;scroll-snap-align:start;display:flex;flex-direction:column;min-height:120px}
.col-h{display:flex;align-items:center;gap:8px;padding:2px 4px 10px;position:sticky;top:0}
.col-h .sw{width:8px;height:8px;border-radius:50%}
.col-h .nm{font-weight:600;font-size:12.5px}
.col-h .ct{margin-left:auto;font:600 11px/1 var(--mono);color:var(--mute);background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:3px 8px;font-variant-numeric:tabular-nums}
.col-b{display:flex;flex-direction:column;gap:10px}
.card{background:var(--panel);border:1px solid var(--cardline);border-left:3px solid var(--c,var(--todo));border-radius:var(--r);padding:11px 12px;box-shadow:var(--shadow);cursor:pointer;transition:transform .12s,box-shadow .12s,border-color .12s;text-align:left;width:100%;display:block}
.card:hover{transform:translateY(-1px);box-shadow:0 4px 14px rgba(16,24,40,.10)}
.card .id{font:600 11px/1 var(--mono);color:var(--faint)}
.card .ti{font-weight:600;font-size:13px;margin:3px 0 0;letter-spacing:-.005em}
.card .bd{display:inline-block;font-size:10.5px;color:var(--mute);background:var(--panel2);border:1px solid var(--line);border-radius:5px;padding:1px 6px;margin-top:7px}
.card .wy{color:var(--mute);font-size:11.5px;margin-top:7px;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.card .mt{display:flex;gap:10px;flex-wrap:wrap;margin-top:9px;padding-top:9px;border-top:1px solid var(--line);font-size:10.5px;color:var(--faint)}
.card .mt .mono{color:var(--mute)}
.emptycol{color:var(--faint);font-size:12px;padding:14px 4px;border:1px dashed var(--line);border-radius:var(--r);text-align:center}
/* timeline */
.tl{margin-top:20px;background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px 18px;box-shadow:var(--shadow)}
.tl-axis{position:relative;height:20px;margin:0 0 6px 190px;border-bottom:1px solid var(--line)}
.tl-axis span{position:absolute;transform:translateX(-50%);font:10px/1 var(--mono);color:var(--faint);top:4px}
.tl-axis span::before{content:"";position:absolute;left:50%;top:-6px;height:6px;border-left:1px solid var(--line)}
.tl-row{display:grid;grid-template-columns:190px 1fr;gap:12px;align-items:center;padding:4px 0}
.tl-row:hover{background:var(--hover);border-radius:6px}
.tl-lb{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.tl-lb .id{font-family:var(--mono);color:var(--faint);font-size:10.5px}
.tl-track{position:relative;height:16px}
.tl-track .grid{position:absolute;top:-4px;bottom:-4px;border-left:1px solid color-mix(in srgb,var(--line) 60%,transparent)}
.tl-bar{position:absolute;top:2px;height:12px;border-radius:6px;background:var(--c,var(--todo));min-width:8px;opacity:.9;cursor:pointer}
.tl-bar.feat{background:linear-gradient(90deg,var(--accent) var(--f,0%),color-mix(in srgb,var(--accent) 26%,transparent) 0)}
.tl-cap{font-size:10.5px;color:var(--faint);margin:10px 0 0 190px}
/* drawer */
.scrim{position:fixed;inset:0;background:rgba(9,14,26,.45);opacity:0;pointer-events:none;transition:opacity .2s;z-index:20}
.scrim.on{opacity:1;pointer-events:auto}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(440px,92vw);background:var(--panel);border-left:1px solid var(--line);box-shadow:-8px 0 30px rgba(9,14,26,.25);transform:translateX(100%);transition:transform .22s cubic-bezier(.2,.7,.2,1);z-index:21;overflow-y:auto;padding:20px 22px}
.drawer.on{transform:none}
.drawer h3{margin:8px 0 2px;font-size:16px;letter-spacing:-.01em}
.dr-close{position:absolute;top:14px;right:14px}
.kv{display:grid;grid-template-columns:96px 1fr;gap:6px 12px;margin:16px 0;font-size:12.5px}
.kv dt{color:var(--faint)}.kv dd{margin:0}
.dr-status{white-space:pre-wrap;background:var(--panel2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;font-size:12.5px;color:var(--mute)}
.chip{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:3px 10px;font-size:11px;font-weight:600;color:#fff}
section.foot{margin-top:26px}
h2{font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--faint);margin:0 0 10px;display:flex;gap:8px;align-items:center}
details>summary{cursor:pointer;list-style:none}details>summary::-webkit-details-marker{display:none}
.frow{display:grid;grid-template-columns:88px 1fr auto;gap:12px;padding:9px 12px;border-top:1px solid var(--line);align-items:start}
.frow:first-child{border-top:0}.panel{background:var(--panel);border:1px solid var(--line);border-radius:10px;overflow:hidden;box-shadow:var(--shadow)}
.empty{padding:16px;color:var(--faint)}
@media (max-width:760px){main{padding:0 16px 56px}header{padding:12px 16px}.hd{padding:0 16px}
  .tl-row,.tl-axis,.tl-cap{grid-template-columns:120px 1fr;margin-left:0}.tl-axis,.tl-cap{margin-left:120px}}
@media (prefers-reduced-motion:reduce){*{transition:none!important}}
</style></head><body>
<header><div class="hd">
  <div class="top">
    <div class="brand"><span class="dot"></span><h1>Friday progress</h1>
      <span class="sync">synced __STAMP__ · <span class="mono">uv run track_progress.py</span></span></div>
    <div class="stats" id="stats"></div>
  </div>
  <div class="tools">
    <label class="search"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
      <input id="q" placeholder="Search tickets, boards, status…  (press /)" aria-label="Search"></label>
    <select id="boardsel" aria-label="Board"></select>
    <span id="filters"></span>
    <span class="seg" role="tablist" aria-label="View">
      <button id="vb" role="tab" aria-pressed="true">Board</button>
      <button id="vt" role="tab" aria-pressed="false">Timeline</button></span>
    <button class="icon" id="theme" aria-label="Toggle theme" title="Toggle theme"></button>
  </div>
</div></header>
<main>
  <div class="attn" id="attn"></div>
  <div id="view"></div>
  <section class="foot"><details id="facts-box"><summary><h2 style="margin:0">Measurements, evals, notes <span class="faint" id="facts-n"></span> ▾</h2></summary>
    <div class="panel" id="facts" style="margin-top:8px"></div></details></section>
</main>
<div class="scrim" id="scrim"></div>
<aside class="drawer" id="drawer" role="dialog" aria-modal="true" aria-label="Ticket detail" tabindex="-1"></aside>
<script>
const rows = __DATA__;
const LABEL = {done:"Done","part-done":"In progress","not-started":"To do",blocked:"Blocked",withdrawn:"Dropped",proposed:"Proposed",unknown:"Unclear"};
const CVAR = {done:"var(--done)","part-done":"var(--partial)","not-started":"var(--todo)",blocked:"var(--blocked)",withdrawn:"var(--dropped)",proposed:"var(--proposed)",unknown:"var(--unknown)",
  open:"var(--blocked)",measurement:"var(--proposed)",eval:"var(--partial)",milestone:"var(--done)",note:"var(--todo)"};
const COLS = ["not-started","part-done","blocked","proposed","unknown","done","withdrawn"];
const URGENCY = ["blocked","part-done","not-started","unknown","proposed"];
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const store = {get(k,d){try{return JSON.parse(localStorage.getItem("fp:"+k))??d}catch{return d}},set(k,v){try{localStorage.setItem("fp:"+k,JSON.stringify(v))}catch{}}};
const tickets = rows.filter(r=>r.kind==="ticket");
const boards = rows.filter(r=>r.kind==="board");
const open = rows.filter(r=>r.kind==="open");
const facts = rows.filter(r=>["measurement","eval","milestone","note"].includes(r.kind));
const live = t=>t.status!=="withdrawn";
const chip = (k,txt)=>`<span class="chip" style="background:${CVAR[k]||"var(--todo)"}">${esc(txt??LABEL[k]??k)}</span>`;
function rel(iso){if(!iso)return"";const s=(Date.now()-new Date(iso))/1000;if(s<0)return"soon";
  const m=s/60,h=m/60,d=h/24,w=d/7,mo=d/30.4,y=d/365;
  if(s<45)return"just now";if(m<45)return Math.round(m)+"m";if(h<24)return Math.round(h)+"h";
  if(d<7)return Math.round(d)+"d";if(w<5)return Math.round(w)+"w";if(mo<12)return Math.round(mo)+"mo";return Math.round(y)+"y";}
function fmt(iso){if(!iso)return"—";const d=new Date(iso);return isNaN(d)?"—":d.toISOString().slice(0,10);}
function ageDays(iso){return iso?Math.max(0,Math.round((Date.now()-new Date(iso))/864e5)):null;}

// theme
let theme = store.get("theme","auto");
function applyTheme(){document.documentElement.setAttribute("data-theme",theme==="auto"?"":theme);
  $("theme").innerHTML = theme==="dark"
    ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12.8A9 9 0 1 1 11.2 3 7 7 0 0 0 21 12.8Z"/></svg>'
    : theme==="light"
    ? '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M4 12H2M22 12h-2M5 5l1.5 1.5M17.5 17.5 19 19M19 5l-1.5 1.5M6.5 17.5 5 19"/></svg>'
    : '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 3v18" fill="currentColor"/></svg>';}
$("theme").onclick = ()=>{theme=theme==="auto"?"light":theme==="light"?"dark":"auto";store.set("theme",theme);applyTheme();};
applyTheme();

// header stats
const liveT = tickets.filter(live), doneN = liveT.filter(t=>t.status==="done").length;
const pct = liveT.length?Math.round(100*doneN/liveT.length):0;
const cnt = s=>tickets.filter(t=>t.status===s).length;
$("stats").innerHTML =
  `<div class="ring" style="--p:${pct}"><i>${pct}%</i></div>`
  +`<div class="stat"><b>${doneN}/${liveT.length}</b><span>tickets done</span></div>`
  +`<div class="stat"><b>${boards.filter(b=>b.status!=="done").length}</b><span>active boards</span></div>`
  +(cnt("blocked")?`<div class="stat"><b style="color:var(--blocked)">${cnt("blocked")}</b><span>blocked</span></div>`:"")
  +(open.length?`<div class="stat"><b style="color:var(--blocked)">${open.length}</b><span>waiting on you</span></div>`:"");

// board selector
let scope = store.get("scope","all");
if(!boards.some(b=>b.board===scope)) scope="all";
$("boardsel").innerHTML = `<option value="all">All boards (${tickets.length})</option>`
  + boards.map(b=>`<option value="${esc(b.board)}">${esc(b.board)} (${tickets.filter(t=>t.board===b.board).length})</option>`).join("");
$("boardsel").value = scope;
$("boardsel").onchange = e=>{scope=e.target.value;store.set("scope",scope);draw();};

// filters
let active = new Set(store.get("filters",[]));
const FILT = ["blocked","part-done","not-started","done"];
function drawFilters(){$("filters").innerHTML = FILT.map(s=>`<button class="pill" data-s="${s}" aria-pressed="${active.has(s)}"><span class="sw" style="background:${CVAR[s]}"></span>${esc(LABEL[s])}</button>`).join("");}
$("filters").onclick = e=>{const b=e.target.closest("[data-s]");if(!b)return;active.has(b.dataset.s)?active.delete(b.dataset.s):active.add(b.dataset.s);store.set("filters",[...active]);drawFilters();draw();};

// view toggle
let view = store.get("view","board");
function setView(v){view=v;store.set("view",v);$("vb").setAttribute("aria-pressed",v==="board");$("vt").setAttribute("aria-pressed",v==="timeline");draw();}
$("vb").onclick=()=>setView("board");$("vt").onclick=()=>setView("timeline");

const qv = ()=>$("q").value.trim().toLowerCase();
const inScope = t=>scope==="all"||t.board===scope;
const hit = t=>inScope(t)&&(!active.size||active.has(t.status))&&(!qv()||[t.board,t.ticket,t.title,t.status_line,LABEL[t.status]].join(" ").toLowerCase().includes(qv()));

const byId = {};
tickets.forEach((t,i)=>{t._i=i;byId[i]=t;});

function card(t){
  const c = CVAR[t.status];
  return `<button class="card" style="--c:${c}" data-i="${t._i}">
    <div class="id">#${esc(t.ticket)}</div>
    <div class="ti">${esc(t.title)}</div>
    ${scope==="all"?`<span class="bd">${esc(t.board)}</span>`:""}
    ${t.status_line&&t.status_line!=="(no status line)"?`<div class="wy">${esc(t.status_line)}</div>`:""}
    <div class="mt">
      <span title="created ${esc(fmt(t.created))}">✦ <span class="mono">${esc(rel(t.created)||"—")}</span></span>
      <span title="last touched ${esc(fmt(t.touched))}">↻ <span class="mono">${esc(rel(t.touched)||"—")}</span></span>
      ${ageDays(t.created)!=null?`<span class="mono" style="margin-left:auto">${ageDays(t.created)}d old</span>`:""}
    </div></button>`;
}

function drawBoard(){
  const show = tickets.filter(hit);
  const cols = COLS.filter(s=>show.some(t=>t.status===s));
  if(!cols.length){$("view").innerHTML=`<div class="empty">Nothing matches.</div>`;return;}
  $("view").innerHTML = `<div class="board-row">`+cols.map(s=>{
    const list = show.filter(t=>t.status===s).sort((a,b)=>String(b.touched).localeCompare(String(a.touched)));
    return `<div class="col"><div class="col-h"><span class="sw" style="background:${CVAR[s]}"></span>
      <span class="nm">${esc(LABEL[s])}</span><span class="ct">${list.length}</span></div>
      <div class="col-b">${list.map(card).join("")||`<div class="emptycol">Empty</div>`}</div></div>`;
  }).join("")+`</div>`;
}

function drawTimeline(){
  // features (boards) when All; otherwise the board's tickets
  let items;
  if(scope==="all"){
    items = boards.map(b=>{const mt=tickets.filter(t=>t.board===b.board&&hit(t));
      const cs=mt.map(t=>t.created).filter(Boolean).sort(), ts=mt.map(t=>t.touched).filter(Boolean).sort();
      const lv=mt.filter(live), dn=lv.filter(t=>t.status==="done").length;
      return mt.length&&cs.length?{feat:true,label:b.board,sub:`${dn}/${lv.length}`,from:cs[0],to:ts[ts.length-1],
        frac:lv.length?Math.round(100*dn/lv.length):0,c:"var(--accent)"}:null;}).filter(Boolean);
  }else{
    items = tickets.filter(hit).filter(t=>t.created).map(t=>({feat:false,label:t.title,sub:"#"+t.ticket,
      from:t.created,to:t.touched||t.created,c:CVAR[t.status],i:t._i})).sort((a,b)=>String(a.from).localeCompare(String(b.from)));
  }
  if(!items.length){$("view").innerHTML=`<div class="empty">Nothing dated in scope.</div>`;return;}
  const lo=Math.min(...items.map(x=>+new Date(x.from))), hiRaw=Math.max(...items.map(x=>+new Date(x.to)),Date.now());
  const pad=(hiRaw-lo)*0.04||864e5, min=lo-pad, max=hiRaw+pad, span=max-min;
  const X=t=>100*(+new Date(t)-min)/span;
  // ~5 gridlines
  const ticks=[];for(let i=0;i<=4;i++){const d=new Date(min+span*i/4);ticks.push({x:i*25,lbl:d.toISOString().slice(5,10)});}
  const grid=ticks.map(t=>`<span style="left:${t.x}%">${t.lbl}</span>`).join("");
  const gridlines=ticks.map(t=>`<div class="grid" style="left:${t.x}%"></div>`).join("");
  $("view").innerHTML = `<div class="tl">
    <div class="tl-axis">${grid}</div>
    ${items.map(x=>{const a=X(x.from),w=Math.max(X(x.to)-a,0.6);
      return `<div class="tl-row"><div class="tl-lb" title="${esc(x.label)}"><span class="id">${esc(x.sub)}</span> ${esc(x.label)}</div>
        <div class="tl-track">${gridlines}<div class="tl-bar ${x.feat?"feat":""}" ${x.feat?`style="left:${a}%;width:${w}%;--f:${x.frac}%"`:`style="left:${a}%;width:${w}%;--c:${x.c}"`+(x.i!=null?` data-i="${x.i}"`:"")}
          title="${esc(fmt(x.from))} → ${esc(fmt(x.to))}"></div></div></div>`;}).join("")}
    <div class="tl-cap">${scope==="all"?"Each bar spans a feature from its first to its latest ticket; fill = % done.":"Each bar spans a ticket from created to last touched."}</div>
  </div>`;
}

function drawAttn(){
  const waits = open.filter(o=>scope==="all"||o.board===scope);
  const blk = tickets.filter(t=>t.status==="blocked"&&inScope(t)).length;
  const parts=[];
  waits.forEach(o=>parts.push(`<span class="a"><b>waiting</b> ${esc(o.board)} · ${esc(o.what)}</span>`));
  if(blk)parts.push(`<span class="a"><b>${blk}</b> blocked in scope</span>`);
  $("attn").innerHTML = parts.join("");
}

function draw(){drawAttn();view==="board"?drawBoard():drawTimeline();}

// drawer
function openDrawer(i){
  const t=byId[i];if(!t)return;
  $("drawer").innerHTML = `<button class="icon dr-close" id="drx" aria-label="Close"><svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6 6 18"/></svg></button>
    <div style="font:600 11px/1 var(--mono);color:var(--faint)">#${esc(t.ticket)} · ${esc(t.board)}</div>
    <h3>${esc(t.title)}</h3>
    <div>${chip(t.status)}${t.label?` <span class="bd">${esc(t.label)}</span>`:""}</div>
    <dl class="kv">
      <dt>Created</dt><dd class="mono">${esc(fmt(t.created))} <span class="faint">(${esc(rel(t.created)||"—")})</span></dd>
      <dt>Last touched</dt><dd class="mono">${esc(fmt(t.touched))} <span class="faint">(${esc(rel(t.touched)||"—")})</span></dd>
      <dt>Age</dt><dd class="mono">${ageDays(t.created)!=null?ageDays(t.created)+" days":"—"}</dd>
      <dt>Path</dt><dd class="mono faint" style="word-break:break-all">${esc(t.path||"—")}</dd>
    </dl>
    <div class="faint" style="font-size:11px;margin-bottom:5px">Status line</div>
    <div class="dr-status">${esc(t.status_line||"(no status line)")}</div>`;
  $("drx").onclick=closeDrawer;
  $("scrim").classList.add("on");$("drawer").classList.add("on");$("drawer").focus();
}
function closeDrawer(){$("scrim").classList.remove("on");$("drawer").classList.remove("on");}
$("scrim").onclick=closeDrawer;
$("view").addEventListener("click",e=>{const el=e.target.closest("[data-i]");if(el)openDrawer(+el.dataset.i);});

$("q").addEventListener("input",draw);
document.addEventListener("keydown",e=>{
  if(e.key==="/"&&document.activeElement!==$("q")){e.preventDefault();$("q").focus();}
  if(e.key==="Escape"){if($("drawer").classList.contains("on"))return closeDrawer();$("q").value="";$("q").blur();draw();}});

// facts, collapsed
const sf = facts.slice().sort((a,b)=>String(b.date).localeCompare(String(a.date)));
$("facts-n").textContent = `(${sf.length})`;
$("facts").innerHTML = sf.map(f=>`<div class="frow"><div>${chip(f.kind,f.kind)}</div>
  <div><div style="font-weight:600"><span class="mute">${esc(f.board)} · </span>${esc(f.what)}</div><div class="mute" style="font-size:12px">${esc(f.source)}</div></div>
  <div class="faint mono" style="font-size:11px">${esc(f.date||"")}</div></div>`).join("")||`<div class="empty">None recorded.</div>`;

drawFilters();setView(view);
</script></body></html>
"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="cmd")
    p_sync = sub.add_parser("sync", help="rebuild board and ticket rows from .scratch")
    p_sync.add_argument("--dry-run", action="store_true", help="print changes, write nothing")
    p_sync.add_argument("--force", action="store_true", help="drop boards gone from .scratch, summaries included")
    p_html = sub.add_parser("html", help="write data/progress.html from the current rows")
    p_html.add_argument("--open", action="store_true", help="open it in the browser")
    args = parser.parse_args(argv)

    try:
        if args.cmd == "sync":
            sync(dry_run=args.dry_run, force=args.force)
        elif args.cmd == "html":
            write_html(load(), open_it=args.open)
        else:
            write_html(sync())
    except Refused as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
