"""Node 5: write the report, and hand the task to the operator.

**A file and a hand-over, and nothing else** (ticket 00). The spec's `Report`
also queues the reporter's brief and a direct message; both wait for ticket
06, because both are things sent under the operator's name and neither is
needed to learn whether the line in front of them works.

The file is the deliverable the slice is scored from: for each of the five
cases it records what was read, what was concluded, and what was not checked
— which are exactly the four questions ticket 00 asks. A run that answers
them in a file the operator can open has done its job even when the
diagnosis is wrong.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from friday.dag.api_issue.diagnose import diagnosis_of
from friday.dag.api_issue.resolve import resolved
from friday.dag.engine import DAGDeps, DAGState, Node, status_of
from friday.domain.actions import HandOver, Reply
from friday.outbox import Kind
from friday.ops.redact import scrub

__all__ = ["render", "report_node"]

log = logging.getLogger(__name__)


def render(state: DAGState, *, task_id: int, at: datetime) -> str:
    """The report, as the operator reads it.

    Ordered the way the run happened — where it looked, what it found, what
    it concluded, what it did not check — because that is the order in which
    a reader stops trusting it. A cause at the top with the evidence below
    reads as an assertion; the evidence first reads as an argument.
    """
    found = state.get("find_request_log", {})
    read = state.get("read_failing_code", {})
    thought = state.get("diagnose", {})
    diagnosis = diagnosis_of(thought)

    try:
        placement, project = resolved(state["resolve"])
        where = (
            f"- environment: `{placement.env}`\n"
            f"- service: `{placement.service}`\n"
            f"- repository: `{(project or {}).get('repo_path', '—')}`"
        )
    except (KeyError, TypeError):
        # A report is written whatever ran, so a state without a `resolve`
        # result renders the rest rather than raising. Narrow on purpose:
        # this used to catch everything, which would have swallowed a real
        # fault in the two lines above it.
        where = "- nothing was resolved"

    lines = [
        f"# api_issue #{task_id}",
        "",
        f"Written {at.isoformat(timespec='seconds')} by Friday. "
        "Nothing here was sent to anybody.",
        "",
        "## Where it looked",
        "",
        where,
        f"- log source: `{found.get('source', '—')}` "
        f"({found.get('kept', 0)} of {found.get('total', 0)} lines kept)",
        "",
        "## What it concluded",
        "",
    ]
    if diagnosis is not None:
        lines += [
            f"**{diagnosis.cause}**",
            "",
            f"- confidence: `{diagnosis.confidence}`",
            f"- conclusive: `{diagnosis.conclusive}`",
            "",
            "Pointing at:",
            *(f"> {line}" for line in thought.get("quotes", [])),
        ]
        if diagnosis.next_checks:
            lines += ["", "Next:", *(f"- {c}" for c in diagnosis.next_checks)]
    else:
        reason = thought.get("reason", "") if isinstance(thought, dict) else ""
        lines.append(f"Nothing. {reason or 'The diagnose node did not run.'}")

    not_checked = list(thought.get("not_checked", [])) if isinstance(thought, dict) else []
    if not not_checked:
        # The diagnose node merges the earlier nodes' lists into its own
        # envelope; when it never ran, they are still owed to the reader.
        for node in (found, read):
            if isinstance(node, dict):
                not_checked += list(node.get("not_checked", []))
                if status_of(node) in {"skipped", "empty", "error"}:
                    not_checked.append(f"{node.get('reason', '')}")

    explained = (read.get("codes") or {}) if isinstance(read, dict) else {}
    counted = found.get("histogram") or []
    if counted:
        lines += ["", "## Every error code in the window, counted", ""]
        lines += [
            f"- `{code}`: {n}"
            + (f" — {explained[code]}" if code in explained else "")
            for code, n in counted
        ]

    lines += ["", "## What it did not check", ""]
    lines += [f"- {line}" for line in not_checked if line] or ["- nothing recorded"]

    lines += [
        "",
        "## The lines it read",
        "",
        "```",
        found.get("dossier", "") or "(none)",
        "```",
        "",
        "## The source it read",
        "",
        "```",
        read.get("code", "") or "(none)",
        "```",
        "",
    ]
    # The dossier is reporter-influenced text and the report is a file the
    # operator opens; `scrub` is what keeps a token that reached a log line
    # from being written to disk a second time.
    return scrub("\n".join(lines))


def brief(diagnosis: Any) -> str:
    """What the reporter is offered, once somebody approves it.

    The cause and nothing else — no dossier, no file path, no frame. They
    asked what was wrong with their request; the evidence is the operator's
    to look at, and a local filesystem path means nothing to them and says
    more about this machine than they need.

    `next_checks` is left out on purpose: it is what *this* investigation
    would do next, which is a note to the operator and reads to a reporter
    as a list of things they have been asked to do.
    """
    said = str(getattr(diagnosis, "cause", "")).strip()
    if not getattr(diagnosis, "conclusive", False):
        # Said plainly rather than hedged into the sentence, so nobody has to
        # notice a missing word to know how far this got.
        said += " (chưa kết luận chắc chắn — cần kiểm thêm)"
    return said


def report_node(
    *, reports_dir: Path, timeout_seconds: float | None = None
) -> Node:
    """Build node 5: the report file, and the two rows that follow it.

    Hands over only when nothing was concluded. With a cause it returns a
    `Reply`, which is how the pool queues the reporter's copy behind an
    approval card — the risk this queue guards is in *answering*, and this
    is the one output that answers.

    `reports_dir` is required rather than defaulted, because there was a
    default here *and* one on `ApiIssueConfig.reports_dir`, spelled
    differently — two answers to one question, which is how a report goes
    missing from the directory somebody is watching.
    """

    async def _report(state: DAGState, deps: DAGDeps) -> Any:
        at = datetime.now(timezone.utc)
        directory = reports_dir
        text = render(state, task_id=deps.task.id, at=at)

        # D10: "a report file at `data/reports/<task_id>.md`". One file per
        # task, so a re-run after the reporter answers replaces the report
        # rather than leaving two that disagree.
        path = directory / f"{deps.task.id}.md"
        try:
            directory.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            written = str(path)
        except OSError as exc:
            # A report that cannot be written is not a run that failed. The
            # hand-over still carries the cause; the operator loses the
            # detail, not the answer.
            log.warning("task %s: could not write the report — %s", deps.task.id, exc)
            written = ""

        thought = state.get("diagnose", {})
        diagnosis = diagnosis_of(thought)
        if diagnosis is not None:
            headline = (
                f"{diagnosis.cause} ({diagnosis.confidence}, "
                f"{'conclusive' if diagnosis.conclusive else 'not conclusive'})"
            )
        else:
            headline = (
                thought.get("reason", "") if isinstance(thought, dict) else ""
            ) or "nothing was diagnosed"

        told = (
            f"{headline}"
            + (f" — the full report is in {written}" if written else "")
        )

        # **The operator is told either way**, and told the one thing the
        # approval card cannot carry: where to read the whole of it. The card
        # shows what the *reporter* would see; this is the reading done
        # before deciding whether they should see it.
        #
        # **`approver`, never `sender`.** `sender` posts into the reporter's
        # channel as the watched account; this row carries the cause and an
        # absolute path on the operator's machine, and it is queued before
        # anything has been approved. Sending it as `sender` — which this did
        # until review caught it — would publish both, unapproved, ahead of
        # the card that asks whether to publish anything at all.
        approver = deps.extra.get("approver", "")
        if approver:
            await deps.db.queue_outbound(
                task_id=deps.task.id,
                conversation=deps.task.conversation,
                kind=Kind.FINDING,
                sender=approver,
                text=told,
            )

        if diagnosis is None:
            # **Nothing concluded, so nothing offered to the reporter.** A
            # brief with no cause in it is a message that costs the operator
            # an approval and tells the reporter that the thing they asked
            # about is still unanswered — which the silence already said.
            return HandOver(told + ". Nothing has been sent to the reporter.")

        return Reply(brief(diagnosis))

    return Node("report", _report, timeout_seconds=timeout_seconds)
