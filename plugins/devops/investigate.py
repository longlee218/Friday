"""The reads `Diagnose` drives itself (spec, "Architecture v3.3").

**A tool is not the back end, and the boundary is a measurement.** On the
captured production case, one raw `loki_query_range` window is 171 KB —
about 43,654 tokens — and what `distil` leaves of it is eight lines. So a
model is handed `read_log(needle, minutes_back)` and never the query tool
underneath: it decides *what* to look for, and code decides *what comes
back*.

**Most of that cutting is the narrowing, not `distil`.** Both back ends
filter where the log is, so nearly every line that arrives already carries
the needle; what `distil` adds here is the error-code histogram and a
ceiling for the case where a back end ignores the filter. Saying it the
other way round — which the first draft of this docstring did — credits the
wrong half and would make the narrowing look optional. That sentence is ticket 15's, and it is the whole of what this
package keeps from the fixed pipeline it replaces.

**Why the pipeline is being replaced at all**, since it works: the formulas
that chose a needle, a window and when to widen were judgement wearing a
rule's clothes, and each was measured getting it wrong inside one week —
the endpoint that matched every other caller (18 dossier lines where the id
gives 8), the 400-line read that covered 84 seconds of the 35 minutes asked
for with the request outside them, and the widening one `WARN` a minute
disarmed for ever. A model that can look, see nothing, and look again
handles those the way a person does.

**Nothing here decides anything about the case.** Which service, which
clone, which databases — all of it arrives from `Resolve` as arguments. A
tool that could choose its own placement is a tool that can read somewhere
nobody meant it to.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from friday.sdk.tools import tool
from plugins.devops.graph.distil import distil
from friday.sdk.sources import Placement
from plugins.devops.sources.code import at_ref, meanings, numbered, original, repo_file

__all__ = ["Evidence", "investigate_tools"]

log = logging.getLogger(__name__)

#: How many reads one investigation may make. Not a cost control: with a
#: fixed pipeline the clock was the sum of a known list of nodes, and a model
#: that can loop turns `api_issue.timeout_seconds` and the token budget from
#: arithmetic into a hope. A run that has looked twelve times and not found
#: it is a run that should say so.
MAX_READS = 12

#: Lines one `read_log` may return, before `distil` cuts. The ceiling the
#: spec puts on `FindRequestLog`, kept: the model chooses the window, code
#: keeps the answer readable.
LOG_LINES = 400

#: How far *past* the report to read. The old node's, and for its reason: a
#: log line is written before the person complains about it, but not always
#: before their clock says so.
MARGIN = timedelta(minutes=5)

#: The widest window a model may ask for. Not distrust — it is how far back
#: any of these back ends keeps anything (Loki is 30 days), and a number
#: past it reads as a search that covered more than it did.
MAX_MINUTES_BACK = 60 * 24 * 30


@dataclass
class Evidence:
    """Every line any tool has shown this run, under the id it was shown as.

    **The grounding gate lives or dies here.** `refs` are line ids and an id
    naming nothing voids the answer — measured, because asked to quote a JSON
    log line the configured model succeeded 32 times in 40 and asked to point
    at one it succeeded 20 in 20. With a fixed pipeline the index was built
    once, from a dossier and a code excerpt. With tools it has to
    **accumulate**: a line shown by the fourth call must be as citable as one
    shown by the first, and ids must never be reused for different text.

    Numbering continues across calls for exactly that reason. `L7` means one
    line for the life of a run, whichever tool produced it.
    """

    index: dict[str, str] = field(default_factory=dict)
    _seen: dict[str, str] = field(default_factory=dict, repr=False)
    #: What was read but not shown — a capped window, a file that is not in
    #: the clone. Merged into the node's own `not_checked`, so the honest
    #: half survives the model choosing what to look at.
    not_checked: list[str] = field(default_factory=list)
    reads: int = 0

    def show(self, lines: Iterable[str]) -> str:
        """Number these lines, continuing this run's numbering.

        Blank lines keep their place and take no id: an id that points at
        nothing is an id a model can cite to mean anything.
        """
        rendered = []
        for raw in lines:
            if not str(raw).strip():
                rendered.append("")
                continue
            text = str(raw)
            # **One id per distinct line, reused.** Windows overlap — a model
            # that widens its search sees what it already saw — and issuing a
            # second id for the same text pays for it twice and offers two
            # ways to cite one thing.
            ref = self._seen.get(text) or f"L{len(self.index) + 1}"
            self._seen[text] = ref
            self.index[ref] = text
            rendered.append(f"{ref} | {raw}")
        return "\n".join(rendered)

    def spent(self) -> str:
        """`""` while there is budget, else the sentence that says there is
        not. Checked by every tool, so the ceiling cannot be forgotten in one
        of them."""
        if self.reads < MAX_READS:
            return ""
        return (
            f"You have made {MAX_READS} reads, which is the limit for one "
            f"investigation. Answer with what you have, and say in "
            f"`next_checks` what you would have looked at next."
        )


def investigate_tools(
    *,
    evidence: Evidence,
    placement: Placement,
    log_sources: dict[str, Any],
    reported_at: Any,
) -> list[Any]:
    """The tools for one run, closed over where this case lives.

    Built per run rather than once, because every one of them needs the
    placement `Intake` produced — and a tool that outlived the run that made
    it would read the previous case's service. `placement` now carries the
    repository, the running tag and the container roots too (ticket 6), so
    there is no separate `project` dict or `release_tag`/`container_roots`
    argument to keep in step with it.
    """
    return [
        _read_log(evidence, placement, log_sources, reported_at),
        _read_code(evidence, placement),
        _what_code_means(evidence, placement),
    ]


def _read_log(
    evidence: Evidence,
    placement: Placement,
    log_sources: dict[str, Any],
    reported_at: Any,
):
    wanted = "loki" if placement.env == "production" else "kubectl"
    source = log_sources.get(wanted)

    @tool
    async def read_log(needle: str, minutes_back: int = 30) -> str:
        """Search this service's log for lines carrying a string.

        The search runs where the log is, so `needle` narrows the *read*
        rather than filtering its result — a whole window is tens of
        thousands of tokens and this returns a handful of lines. Give the
        most specific thing you have: a correlationId names one request, an
        id names one user, an endpoint path names everyone who called it.

        Nothing is found and you think the window is wrong? Call again with
        a larger `minutes_back`. Nothing is found twice? Say so; the log
        reaching back and holding nothing is a fact about the request.

        Args:
            needle: a plain substring of the log line — an id, a path, an
                error code. Not a regular expression.
            minutes_back: how far before the report to search. The default
                is thirty; six hours is 360. Thirty days is the most any of
                these back ends keeps.
        """
        if spent := evidence.spent():
            return spent
        if source is None:
            return (
                f"No {wanted} log source is configured, so {placement.env} "
                f"logs cannot be read at all."
            )
        evidence.reads += 1
        # Not `wanted`: that names the back end in the enclosing scope, and
        # assigning it here made it local for the whole function — so the
        # "no source configured" branch above raised `UnboundLocalError`
        # instead of saying which source it wanted. A test caught it.
        window = min(MAX_MINUTES_BACK, max(1, int(minutes_back)))
        since = reported_at - timedelta(minutes=window)
        try:
            found = await source.lines(
                placement, since=since, until=reported_at + MARGIN,
                limit=LOG_LINES, needle=needle,
            )
        except Exception as exc:  # noqa: BLE001 — a source that is down is an answer
            log.warning("read_log(%r) failed: %s", needle, exc)
            return f"The log could not be read: {type(exc).__name__}: {exc}"

        # **Out of reach is not not-found**, and it is the difference between
        # a request nobody can find and one nobody searched for.
        if not found.lines and found.oldest is not None and found.oldest > since:
            evidence.not_checked.append(
                f"the log does not reach back to {since:%Y-%m-%dT%H:%M:%SZ}"
            )
            return (
                f"Nothing, and the log does not reach back that far — its "
                f"oldest line is {found.oldest:%Y-%m-%dT%H:%M:%SZ}. This "
                f"request was not searched for; it is no longer there to "
                f"search."
            )
        if not found.lines:
            return f"No line in the last {minutes_back} minutes carries {needle!r}."

        cut = distil(list(found.lines), matching=(needle,), max_lines=LOG_LINES)
        if found.truncated:
            evidence.not_checked.append(
                f"the search for {needle!r} was capped at {LOG_LINES} lines"
            )
        if not cut.lines:
            # A back end that ignores `needle` is allowed to (the protocol
            # says so), and then nothing in the window is "ours" and nothing
            # is loud. Returning the empty string tells a model nothing at
            # all — the old node had this branch and it is not optional.
            return (
                f"{len(found.lines)} lines came back but none of them carries "
                f"{needle!r}. The source may not support narrowing; try a "
                f"different string."
            )
        said = [evidence.show(cut.lines)]
        if cut.histogram:
            said.append(
                "\nError codes in what was read: "
                + ", ".join(f"{code} ×{n}" for code, n in cut.histogram)
            )
        for line in cut.not_checked:
            evidence.not_checked.append(line)
        return "\n".join(said)

    return read_log


def _read_code(evidence: Evidence, placement: Placement):
    repo_path = placement.repo_path
    release_tag = placement.release_tag
    container_roots = placement.container_roots

    @tool
    def read_code(file: str, line: int) -> str:
        """Read the code around a line, as the running version has it.

        Give the file exactly as a stack frame named it. A compiled frame —
        `dist/src/x.js:80` — is mapped back to the TypeScript before it is
        read, because line 80 of the built file is not the line anybody
        wrote.

        **What comes back is the deployed version where that can be
        resolved**, not whatever the clone is checked out at. The answer says
        which.

        Args:
            file: the path from the stack frame.
            line: the line number from the stack frame.
        """
        if spent := evidence.spent():
            return spent
        if not repo_path:
            return "No repository is recorded for this service, so no code can be read."
        evidence.reads += 1
        found = repo_file(file, repo_path, container_roots=container_roots)
        if found is None:
            return (
                f"{file} is not in this clone — it is somebody else's code, "
                f"or outside the repository."
            )
        shown, at = found, int(line)
        mapped = original(found, int(line), repo_path)
        if mapped is not None:
            shown, at = mapped
        try:
            here = shown.read_text(errors="replace")
        except OSError as exc:
            return f"{file} could not be read: {exc}"
        there = at_ref(repo_path, shown, release_tag) if release_tag else None
        if there is None:
            text, whose = here, "the clone's current checkout"
            if release_tag:
                evidence.not_checked.append(
                    f"{shown.name} does not exist at the running tag {release_tag}"
                )
        elif there == here:
            text, whose = here, f"identical to the running tag {release_tag}"
        else:
            text, whose = there, f"as it is at the running tag {release_tag}"
        return (
            f"--- {shown.name}:{at} ({whose})\n"
            + evidence.show(numbered(text, at).splitlines())
        )

    return read_code


def _what_code_means(evidence: Evidence, placement: Placement):
    doc = placement.error_code_doc
    repo_path = placement.repo_path

    @tool
    def what_code_means(code: str) -> str:
        """What one of this project's error codes stands for.

        Read out of the repository's own table, not guessed. Measured: every
        one of 2,104 HTTP 500s in thirty days carried `ERR19`, the generic
        code — so a number on its own says almost nothing, and what the
        table calls it can say a great deal.

        Args:
            code: the error code exactly as the log spelled it, e.g. `ERR19`.
        """
        if spent := evidence.spent():
            return spent
        evidence.reads += 1
        if not doc:
            return "This project records no error-code table."
        found = meanings(doc, [code], repo_path)
        return found.get(code) or f"{code} is not in this project's table."

    return what_code_means
