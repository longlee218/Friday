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
from datetime import date
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


# ── the page ────────────────────────────────────────────────────────────────

def render(rows: list[dict]) -> str:
    """One self-contained page: the rows embedded, no network, no build step."""
    # Every "<" escaped, not only "</": "<!--<script>" in a status line would
    # otherwise swallow the real </script> and blank the page.
    data = json.dumps(rows, ensure_ascii=False).replace("<", "\\u003c")
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
:root{--bg:#f6f6f4;--panel:#fff;--ink:#1c1c1e;--mute:#6e6e73;--line:#e5e5e2;--hover:#f0f0ed;
--done:#2f9e5e;--partial:#d8961c;--todo:#9aa0a6;--blocked:#d1433a;--dropped:#c9c9c9;--proposed:#6a7fd8;--unknown:#8f5bd0}
@media (prefers-color-scheme:dark){:root{--bg:#131315;--panel:#1b1b1e;--ink:#ececef;--mute:#9b9ba3;--line:#2b2b30;--hover:#232327;--dropped:#46464c}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:13.5px/1.45 -apple-system,system-ui,"Segoe UI",sans-serif}
main{max-width:980px;margin:0 auto;padding:0 16px 56px}
header{position:sticky;top:0;z-index:2;background:var(--bg);padding:14px 0 10px;border-bottom:1px solid var(--line)}
.top{display:flex;align-items:baseline;justify-content:space-between;gap:12px;flex-wrap:wrap}
h1{font-size:17px;margin:0}.mute{color:var(--mute)}.small{font-size:12px}
.stats{display:flex;gap:14px;flex-wrap:wrap;font-size:12.5px}.stats b{font-size:14px}
.bar{display:flex;height:6px;border-radius:3px;overflow:hidden;background:var(--line)}
.bar i{display:block;height:100%}.bar.big{height:8px;margin-top:10px}
.tools{display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:10px}
.tools input{flex:1;min-width:160px;background:var(--panel);color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:5px 8px;font:inherit}
.pill{border:1px solid var(--line);background:var(--panel);color:var(--ink);border-radius:12px;padding:3px 9px;font:inherit;font-size:12px;cursor:pointer}
.pill[aria-pressed=true]{border-color:var(--ink)}
section{margin-top:22px}h2{font-size:12px;letter-spacing:.06em;text-transform:uppercase;color:var(--mute);margin:0 0 8px;display:flex;gap:8px;align-items:center}
.panel{background:var(--panel);border:1px solid var(--line);border-radius:8px;overflow:hidden}
.row{display:grid;grid-template-columns:78px 1fr auto;gap:10px;padding:8px 12px;border-top:1px solid var(--line);align-items:start}
.row:first-child{border-top:0}.row:hover{background:var(--hover)}
.row .t{font-weight:600}.row .why{color:var(--mute);font-size:12.5px;margin-top:1px}
.chip{display:inline-block;border-radius:10px;padding:1px 7px;font-size:11px;font-weight:600;color:#fff;text-align:center;min-width:58px}
.tag{font-size:11px;color:var(--mute);white-space:nowrap}
.board{display:grid;grid-template-columns:1fr 150px 64px 92px 16px;gap:12px;align-items:center;padding:9px 12px;border-top:1px solid var(--line);cursor:pointer}
.board:first-child{border-top:0}.board:hover{background:var(--hover)}.board .name{font-weight:600}
.board .caret{color:var(--mute);transition:transform .15s}.board[aria-expanded=true] .caret{transform:rotate(90deg)}
.detail{padding:4px 12px 12px 12px;border-top:1px dashed var(--line);background:var(--bg)}
.detail .summary{color:var(--mute);font-size:12.5px;margin:8px 0}
.detail .row{background:var(--panel);border:1px solid var(--line);border-radius:6px;margin-top:6px}
.link{background:none;border:0;color:var(--mute);text-decoration:underline;cursor:pointer;font:inherit;font-size:12px;padding:0}
.empty{padding:14px 12px;color:var(--mute)}
details>summary{cursor:pointer;list-style:none}details>summary::-webkit-details-marker{display:none}
@media (max-width:640px){.board{grid-template-columns:1fr 70px 16px}.board .hide-s{display:none}.row{grid-template-columns:64px 1fr}.row .tag{grid-column:2}}
</style></head><body><main>
<header>
  <div class="top"><h1>Friday progress</h1><span class="mute small">synced __STAMP__ · <code>uv run track_progress.py</code></span></div>
  <div class="bar big" id="allbar"></div>
  <div class="top" style="margin-top:8px"><div class="stats" id="stats"></div></div>
  <div class="tools">
    <input id="q" placeholder="Search tickets, boards, status…  (press /)">
    <span id="filters"></span>
  </div>
</header>

<section><h2>Needs attention <span id="attn-n"></span></h2><div class="panel" id="attention"></div></section>
<section><h2>Boards</h2><div class="panel" id="boards"></div></section>
<section><h2>Recently moved</h2><div class="panel" id="recent"></div></section>
<section><details id="facts-box"><summary><h2 style="margin:0">Measurements, evals, notes <span class="mute" id="facts-n"></span> ▸</h2></summary>
  <div class="panel" id="facts" style="margin-top:8px"></div></details></section>
</main>
<script>
const rows = __DATA__;
const LABEL = {done:"done","part-done":"partial","not-started":"to do",blocked:"blocked",withdrawn:"dropped",proposed:"proposed",unknown:"unclear"};
const COLOR = {done:"var(--done)","part-done":"var(--partial)","not-started":"var(--todo)",blocked:"var(--blocked)",
  withdrawn:"var(--dropped)",proposed:"var(--proposed)",unknown:"var(--unknown)",open:"var(--blocked)",
  measurement:"var(--proposed)",eval:"var(--partial)",milestone:"var(--done)",note:"var(--todo)"};
const URGENCY = ["blocked","part-done","not-started","unknown","proposed"];
const ORDER = ["done","part-done","not-started","blocked","unknown","proposed","withdrawn"];
const $ = id => document.getElementById(id);
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const chip = (k, text) => `<span class="chip" style="background:${COLOR[k] || "var(--todo)"}">${esc(text ?? LABEL[k] ?? k)}</span>`;
const store = {get(k, d){try{return JSON.parse(localStorage.getItem("fp:" + k)) ?? d}catch{return d}},
               set(k, v){try{localStorage.setItem("fp:" + k, JSON.stringify(v))}catch{}}};

const tickets = rows.filter(r => r.kind === "ticket");
const boards = rows.filter(r => r.kind === "board");
const open = rows.filter(r => r.kind === "open");
const facts = rows.filter(r => ["measurement","eval","milestone","note"].includes(r.kind));
const live = t => t.status !== "withdrawn";

function bar(list){
  const n = list.length || 1, c = {};
  list.forEach(t => c[t.status] = (c[t.status] || 0) + 1);
  return ORDER.filter(s => c[s]).map(s => `<i title="${esc(LABEL[s])}: ${c[s]}" style="width:${100 * c[s] / n}%;background:${COLOR[s]}"></i>`).join("");
}
function lastDate(list){ return list.map(t => t.updated).filter(Boolean).sort().pop() || ""; }

// header
const liveT = tickets.filter(live), doneN = liveT.filter(t => t.status === "done").length;
$("allbar").innerHTML = bar(tickets);
const count = s => tickets.filter(t => t.status === s).length;
$("stats").innerHTML = [
  `<span><b>${doneN}/${liveT.length}</b> done (${liveT.length ? Math.round(100 * doneN / liveT.length) : 0}%)</span>`,
  `<span><b>${boards.filter(b => b.status !== "done").length}</b>/${boards.length} boards active</span>`,
  ...["blocked","part-done","not-started","unknown"].filter(s => count(s)).map(s =>
    `<span style="color:${COLOR[s]}"><b>${count(s)}</b> ${LABEL[s]}</span>`),
  open.length ? `<span style="color:var(--blocked)"><b>${open.length}</b> waiting on you</span>` : ""].join("");

// filters
let active = new Set(store.get("filters", []));
const FILTERS = ["blocked","part-done","not-started","done","withdrawn"];
function drawFilters(){
  $("filters").innerHTML = FILTERS.map(s => `<button class="pill" data-s="${s}" aria-pressed="${active.has(s)}">${esc(LABEL[s])}</button>`).join("")
    + (active.size ? ` <button class="link" id="clear">clear</button>` : "");
}
$("filters").addEventListener("click", e => {
  const b = e.target.closest("[data-s]");
  if (b) { active.has(b.dataset.s) ? active.delete(b.dataset.s) : active.add(b.dataset.s); }
  else if (e.target.id === "clear") active.clear();
  else return;
  store.set("filters", [...active]); drawFilters(); draw();
});

const q = () => $("q").value.trim().toLowerCase();
const hit = t => (!active.size || active.has(t.status)) &&
  (!q() || [t.board, t.ticket, t.title, t.status_line, LABEL[t.status]].join(" ").toLowerCase().includes(q()));
const ticketRow = (t, withBoard) => `<div class="row"><div>${chip(t.status)}</div>
  <div><div class="t">${withBoard ? `<span class="mute">${esc(t.board)} · </span>` : ""}#${esc(t.ticket)} ${esc(t.title)}</div>
  <div class="why">${esc(t.status_line)}</div></div>
  <div class="tag" title="${esc(t.path)}">${esc(t.updated || "")}</div></div>`;

let expanded = new Set(store.get("expanded", []));
let showAll = new Set(store.get("showAll", []));

function draw(){
  // needs attention: unfinished tickets by urgency, then what waits on the operator
  const attn = tickets.filter(t => URGENCY.includes(t.status) && hit(t))
    .sort((a, b) => URGENCY.indexOf(a.status) - URGENCY.indexOf(b.status) || String(b.updated).localeCompare(String(a.updated)));
  const waits = open.filter(o => !q() || (o.board + " " + o.what).toLowerCase().includes(q()));
  $("attn-n").innerHTML = `<span class="mute">${attn.length + waits.length}</span>`;
  $("attention").innerHTML = (waits.map(o => `<div class="row"><div>${chip("open", "waiting")}</div>
      <div><div class="t"><span class="mute">${esc(o.board)} · </span>${esc(o.what)}</div><div class="why">${esc(o.source)}</div></div>
      <div class="tag">${esc(o.date || "")}</div></div>`).join("") + attn.map(t => ticketRow(t, true)).join(""))
    || `<div class="empty">Nothing unfinished matches.</div>`;

  // boards: one line each; open to see the unfinished tickets, or all of them
  $("boards").innerHTML = boards.map(b => {
    const mine = tickets.filter(t => t.board === b.board);
    const shown = mine.filter(hit);
    if ((q() || active.size) && !shown.length && !b.board.includes(q())) return "";
    const lv = mine.filter(live), dn = lv.filter(t => t.status === "done").length;
    const isOpen = expanded.has(b.board) || ((q() || active.size) && shown.length);
    const all = showAll.has(b.board);
    const list = (all || active.size || q()) ? shown : shown.filter(t => t.status !== "done" && t.status !== "withdrawn");
    const hidden = shown.length - list.length;
    return `<div class="board" data-b="${esc(b.board)}" aria-expanded="${!!isOpen}">
        <span class="name">${esc(b.board)}</span>
        <span class="bar hide-s">${bar(mine)}</span>
        <span class="small ${dn === lv.length ? "" : "mute"}" style="text-align:right">${dn}/${lv.length}</span>
        <span class="tag hide-s">${esc(lastDate(mine) || b.dates || "")}</span>
        <span class="caret">›</span></div>
      ${isOpen ? `<div class="detail"><div class="summary">${esc(b.summary)}</div>
        ${list.map(t => ticketRow(t, false)).join("") || `<div class="empty">Every ticket is done.</div>`}
        <div style="margin-top:8px" class="small">
          ${hidden ? `<button class="link" data-all="${esc(b.board)}">show ${hidden} finished</button> · ` : ""}
          ${all && !active.size && !q() ? `<button class="link" data-all="${esc(b.board)}">hide finished</button> · ` : ""}
          <span class="mute">spec ${esc(b.spec || "–")}</span></div></div>` : ""}`;
  }).join("") || `<div class="empty">No board matches.</div>`;

  // recently moved: tickets and milestones by date, newest first
  const moved = [...tickets.filter(t => t.updated && hit(t)).map(t => ({date: t.updated, html: ticketRow(t, true)})),
                 ...facts.filter(f => f.kind === "milestone").map(f => ({date: f.date, html:
                   `<div class="row"><div>${chip("milestone", "milestone")}</div><div><div class="t"><span class="mute">${esc(f.board)} · </span>${esc(f.what)}</div></div><div class="tag">${esc(f.date)}</div></div>`}))]
    .sort((a, b) => String(b.date).localeCompare(String(a.date))).slice(0, 12);
  $("recent").innerHTML = moved.map(m => m.html).join("") || `<div class="empty">Nothing dated.</div>`;
}

$("boards").addEventListener("click", e => {
  const all = e.target.closest("[data-all]");
  if (all) { const b = all.dataset.all; showAll.has(b) ? showAll.delete(b) : showAll.add(b); store.set("showAll", [...showAll]); draw(); return; }
  const head = e.target.closest(".board");
  if (!head) return;
  const b = head.dataset.b; expanded.has(b) ? expanded.delete(b) : expanded.add(b);
  store.set("expanded", [...expanded]); draw();
});
$("q").addEventListener("input", draw);
document.addEventListener("keydown", e => {
  if (e.key === "/" && document.activeElement !== $("q")) { e.preventDefault(); $("q").focus(); }
  if (e.key === "Escape") { $("q").value = ""; $("q").blur(); draw(); }
});

// measurements and notes, collapsed
const sorted = facts.slice().sort((a, b) => String(b.date).localeCompare(String(a.date)));
$("facts-n").textContent = `(${sorted.length})`;
$("facts").innerHTML = sorted.map(f => `<div class="row"><div>${chip(f.kind, f.kind)}</div>
  <div><div class="t"><span class="mute">${esc(f.board)} · </span>${esc(f.what)}</div><div class="why">${esc(f.source)}</div></div>
  <div class="tag">${esc(f.date || "")}</div></div>`).join("") || `<div class="empty">None recorded.</div>`;

drawFilters(); draw();
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
