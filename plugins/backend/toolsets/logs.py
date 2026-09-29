"""`backend.logs`: log lines, from the two places this system may read them (D4).

Production through the devops MCP's Loki tools; dev through `kubectl` on the
dev host, reached by `ssh dev` — not a kubeconfig on this machine, which the
spec assumed and ticket 16 measured to be wrong.

**One file per data source, tools and client together** (board
`domains-plug-in` ticket 09 §3): the two sources below and `read_log`, the one
tool a model calls over them. Over the 200-line soft target for that reason.

Both are reads, and the guard is the absence of a verb rather than a line in
a prompt: there is nothing here that writes to a cluster. Neither knows what
it is being read for.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shlex
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from friday.sdk.evidence import Evidence
from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS, Lines
from friday.sdk.toolset import RunContext, ToolsetSpec, tool
from plugins.backend.graph.distil import distil
from plugins.backend.placement import Placement

__all__ = [
    "LOGS",
    "LOKI_SERVER",
    "LOKI_TOOL",
    "SSH_HOST",
    "LokiSource",
    "SshKubectlSource",
    "log_sources",
    "log_tools",
]

log = logging.getLogger(__name__)

#: The SSH alias `kubectl` runs behind for dev, from `~/.ssh/config`.
#: Constants since build-the-spine ticket 09: they were `backend:` settings
#: (`ssh_host`, `loki_server`, `loki_tool`), install facts nobody changed.
SSH_HOST = "dev"
#: The MCP server that carries Loki, and its range-query tool.
LOKI_SERVER = "devops-generic"
LOKI_TOOL = "loki_query_range"

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


@dataclass(frozen=True, slots=True)
class SshKubectlSource:
    """Dev: `kubectl` on the dev host, over SSH.

    Measured on 2026-09-18 from the operator's Mac (ticket 16): `get pods -A`
    1.5 s, `logs --tail=200` 1.2 s, `logs --since=6h` 1.5 s, each including
    the handshake. Two round trips per read, because the pod name is a
    pattern until something looks it up.

    The host is an alias in `~/.ssh/config`, non-interactive with the agent
    loaded. Nothing here supplies a password or a key: if the alias does not
    resolve, the command fails and the node's envelope says so.
    """

    name: str = "kubectl"
    host: str = SSH_HOST

    async def lines(
        self,
        placement: Placement,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        needle: str = "",
    ) -> Lines:
        pod = await self._run(
            f"kubectl -n {shlex.quote(placement.namespace)} get pods "
            f"-o name | grep {shlex.quote(placement.pod_pattern)} | head -1"
        )
        pod = pod.strip()
        if not pod:
            raise RuntimeError(
                f"no pod matching {placement.pod_pattern!r} in namespace "
                f"{placement.namespace!r} on {self.host}"
            )
        # `--since-time` rather than `--since`: kubectl's relative form is
        # relative to *now*, and the window this node wants is around the
        # reporter's message.
        #
        # `--timestamps` is what makes the upper bound possible at all. There
        # is no `--until`, and `--tail` counts from the *newest* line — so a
        # window that opened sixteen hours ago came back as the newest 400
        # lines of today, and the node reported them as if they were the
        # reporter's. The container runtime's own stamp is also
        # format-independent, which the line's own `"time"` field is not:
        # dev is JSON today and Python tracebacks tomorrow.
        #
        # **With a needle, `--tail` is the wrong bound and `grep` is the
        # right one.** `--tail` is applied by the API server *before*
        # anything downstream sees a line, so `--tail=400 | grep` searches
        # the newest 400 lines and not the window. Asking for the whole
        # window and letting `grep` narrow it on the far side searches all
        # of it, and only the matching lines cross the network — `head`
        # keeps the answer bounded whatever matches.
        #
        # **`pipefail`, and it is not decoration.** A pipeline exits with its
        # *last* command's status, so `head` returning 0 would hide whatever
        # `kubectl` did: a pod that has gone, an RBAC denial, the wrong
        # container. Each of those would arrive as empty output and be
        # reported as "the window holds nothing about this request" — the
        # exact confusion between *not found* and *not searched* that the
        # retention check upstream exists to prevent. `bash -o pipefail -c`
        # rather than a bare `set -o pipefail`, because the shell `ssh`
        # starts is the login shell and need not be one that has it.
        #
        # `grep` alone exits 1 when it matches nothing, which under
        # `pipefail` *would* be an error — so the match is allowed to be
        # empty explicitly, and only kubectl's own failure is one.
        read = (
            f"kubectl -n {shlex.quote(placement.namespace)} logs "
            f"{shlex.quote(pod)} --timestamps "
            f"--since-time={shlex.quote(_rfc3339(since))} "
        )
        if needle:
            piped = (
                f"{read} --tail=-1 | "
                f"{{ grep -F -- {shlex.quote(needle)} || true; }} | "
                f"head -n {int(limit)}"
            )
            out = await self._run(f"bash -o pipefail -c {shlex.quote(piped)}")
        else:
            out = await self._run(f"{read} --tail={int(limit)}")

        # **The cap is silent, so it has to be inferred.** `kubectl` says
        # nothing about having truncated, where Loki says `truncated: true`.
        # Exactly `limit` lines back from a bound of `limit` is the only
        # signal there is, and reporting a full read as capped costs a
        # sentence where missing a capped one costs a dossier believed to be
        # the window.
        raw = out.splitlines()
        return _within(raw, since=since, until=until, truncated=len(raw) >= int(limit))

    async def _run(self, remote: str) -> str:
        proc = await asyncio.create_subprocess_exec(
            "ssh",
            "-o",
            "BatchMode=yes",
            self.host,
            remote,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(
                proc.communicate(), timeout=TOOL_CALL_TIMEOUT_SECONDS
            )
        except TimeoutError:
            proc.kill()
            # Reaped, not merely killed: without this the child stays a
            # zombie for the life of the process, and this runs on every
            # dev task.
            await proc.wait()
            raise
        if proc.returncode != 0:
            raise RuntimeError(
                f"ssh {self.host}: exit {proc.returncode} — "
                f"{err.decode(errors='replace').strip()[:400]}"
            )
        return out.decode(errors="replace")


@dataclass(frozen=True, slots=True)
class LokiSource:
    """Production: the devops MCP's Loki tools.

    **The tool's name is configuration; its arguments are not.** The session
    that measured this server (ticket 16) read its catalogue but did not run a
    query through it, so the one thing that would make either safe — a call
    that came back — has not happened. The name is the half an operator can
    correct in `config.yaml`; wrong argument names are a release, and ticket
    02 is where they stop being a guess.

    Bounded on purpose: `loki_series` over seven days is ~780 KB, and the
    spec says never to ask it unbounded from a graph.

    **Measured against the real server, 2026-09-21.** The tool's name and its
    four argument names were guesses when this was written and all four came
    back right. Three things did not:

    - the cluster label is `apero_cluster`, not `cluster`. This Loki
      aggregates every cluster, so the wrong label name matches nothing and
      the right one is the difference between one product's logs and seven
      clusters merged.
    - the answer is a JSON object of *streams*, one per replica, not a flat
      block of text. `splitlines()` on it returned JSON fragments.
    - every line arrives as `"<iso> <line>"`, the same shape `kubectl
      --timestamps` produces — which is what lets one function strip both.
    """

    #: **What this class may call, declared here and nowhere else.** The
    #: server is filtered to it and every call is checked against it, so
    #: `config.yaml` can choose among these names and cannot add one.
    TOOLS = frozenset({"loki_query_range"})

    server: Any
    name: str = "loki"
    tool: str = "loki_query_range"
    #: LogQL. `{...}` is filled with the placement's labels.
    query: str = '{{apero_cluster="{cluster}", namespace="{namespace}", app="{app}"}}'

    async def lines(
        self,
        placement: Placement,
        *,
        since: datetime,
        until: datetime,
        limit: int,
        needle: str = "",
    ) -> Lines:
        selector = self.query.format(
            cluster=placement.cluster,
            namespace=placement.namespace,
            app=placement.app,
        )
        if needle:
            # LogQL's line filter, which runs where the log is. This is the
            # difference measured on 2026-09-21 between 400 lines covering
            # three per cent of the window and not containing the request,
            # and two lines containing all of it with `truncated: false`.
            selector = f'{selector} |= "{_logql(needle)}"'
        result = await self.server.call(
            self.tool,
            {
                "query": selector,
                "start": _rfc3339(since),
                "end": _rfc3339(until),
                "limit": int(limit),
            },
        )
        # No clipping: Loki is asked for both ends of the window and honours
        # them. The stamps still come off, and the streams still merge — one
        # stream per replica, and a dossier that interleaves replicas in
        # whatever order they arrived is one whose surrounding lines belong
        # to a different process than the line they surround.
        return _streams(_text_of(result))


def _reported_at(task: Any) -> datetime:
    """When the reporter said something — what D5's window is measured back
    from.

    The task's own creation time, which is within seconds of the message that
    opened it. `now` is the fallback for a task with no timestamp at all, and
    it is the wrong answer often enough to be worth a log line rather than a
    silent substitution.
    """
    at = getattr(task, "created_at", None)
    if isinstance(at, str):
        try:
            at = datetime.fromisoformat(at)
        except ValueError:
            at = None
    if not isinstance(at, datetime):
        log.warning(
            "task %s has no usable created_at — reading the log around now "
            "instead, which is not when this was reported",
            getattr(task, "id", "?"),
        )
        return datetime.now(UTC)
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def _said(window: timedelta) -> str:
    hours, seconds = divmod(int(window.total_seconds()), 3600)
    return f"{hours}h" if hours and not seconds else f"{seconds // 60}m"


def _rfc3339(at: datetime) -> str:
    """The one time format both sides of this module speak."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of(result: Any) -> str:
    """Whatever an MCP tool answered, as text.

    Duck-typed rather than imported: `friday/kernel/harness/harness.py` is the one
    module that imports the SDK, and a second one here would make the SDK's
    result shape load-bearing in a graph node.
    """
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(getattr(part, "text", part))
            for part in content
        )
    return str(content)


#: `--timestamps` prefixes every line with an RFC3339 stamp and a space.
_STAMPED = re.compile(r"^(?P<at>\d{4}-\d\d-\d\dT[\d:.]+Z) (?P<line>.*)$")


def _streams(answered: str) -> Lines:
    """Loki's compacted answer, as lines in time order.

    `{"streams": [{"labels": …, "lines": ["<iso> <line>", …]}, …],
    "truncated": bool}` — captured from the real server on 2026-09-21 and
    kept in `tests/test_trace_problem.py` as a fixture, because a shape this
    module parses is a shape that has to be checked against the thing that
    produces it rather than against what its documentation says.

    An answer that does not parse is empty rather than an exception: a
    changed shape should read as "Loki said nothing I understood", which the
    node reports, and not as a crashed graph.
    """
    try:
        answer = json.loads(answered)
    except (TypeError, ValueError):
        log.warning("loki answered something that is not JSON")
        return Lines()
    if not isinstance(answer, dict):
        return Lines()

    stamped: list[tuple[datetime, str]] = []
    oldest: datetime | None = None
    newest: datetime | None = None
    for stream in answer.get("streams") or []:
        for raw in stream.get("lines") or []:
            at, _, line = str(raw).partition(" ")
            try:
                when = datetime.fromisoformat(at)
            except ValueError:
                stamped.append((datetime.max.replace(tzinfo=UTC), str(raw)))
                continue
            oldest = when if oldest is None else min(oldest, when)
            newest = when if newest is None else max(newest, when)
            stamped.append((when, line))
    stamped.sort(key=lambda pair: pair[0])
    return Lines(
        tuple(line for _, line in stamped),
        oldest,
        newest,
        bool(answer.get("truncated")),
    )


def _within(
    stamped: list[str],
    *,
    since: datetime,
    until: datetime,
    truncated: bool = False,
) -> Lines:
    """The lines inside the window, with their stamps taken back off, and the
    span the back end actually handed over.

    `oldest` and `newest` are of what *arrived*, whether or not it was inside
    the window: they describe the read, and the whole point of them is to be
    comparable against the window that was asked for.

    A line with no stamp is kept: `kubectl` writes one warning of its own
    ("Defaulted container …") ahead of the log, and dropping unparseable
    lines silently is how a format change becomes an empty dossier nobody
    can explain.
    """
    kept: list[str] = []
    oldest: datetime | None = None
    newest: datetime | None = None
    for raw in stamped:
        found = _STAMPED.match(raw)
        if found is None:
            kept.append(raw)
            continue
        at = datetime.fromisoformat(found.group("at"))
        oldest = at if oldest is None else min(oldest, at)
        newest = at if newest is None else max(newest, at)
        if since <= at <= until:
            kept.append(found.group("line"))
    return Lines(tuple(kept), oldest, newest, truncated)


def _logql(needle: str) -> str:
    """A string, safe to sit inside LogQL's double-quoted line filter.

    Go's quoted-string rules, which is what Loki parses: a backslash and a
    double quote are the two characters that end or extend the literal, and
    a needle carrying either would otherwise change the query rather than be
    searched for. Backslash first, or escaping the quote would then have its
    own backslash escaped.
    """
    return needle.replace("\\", "\\\\").replace('"', '\\"')


def _rfc3339(at: datetime) -> str:
    """The one time format both back ends speak."""
    return at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text_of(result: Any) -> str:
    """Whatever an MCP tool answered, as text.

    Duck-typed rather than imported: `friday/kernel/harness/harness.py` is the one
    module that may import the SDK, and this package may not import
    `friday/kernel/harness/` at all.
    """
    content = getattr(result, "content", result)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else str(getattr(part, "text", part))
            for part in content
        )
    return str(content)


def log_sources(run: RunContext) -> dict[str, Any]:
    """The two ways to read a log (D4): `kubectl` over SSH always, Loki when
    its server is open this run — narrowed by the core to `LokiSource.TOOLS`."""
    sources: dict[str, Any] = {"kubectl": SshKubectlSource(host=SSH_HOST)}
    reads = run.mcp.get(LOKI_SERVER)
    if reads is not None:
        sources["loki"] = LokiSource(server=reads, tool=LOKI_TOOL)
    return sources


def log_tools(run: RunContext, sources: dict[str, Any]) -> list:
    """`read_log` over `sources`. Split from the factory so a replay (a
    captured case) or a test hands its own sources to the same tool."""
    return [_read_log(run.evidence, run.domain, sources, run.reported_at)]


def _read_log(
    evidence: Evidence,
    placement: Placement,
    sources: dict[str, Any],
    reported_at: datetime,
):
    wanted = "loki" if placement.env == "production" else "kubectl"
    source = sources.get(wanted)

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
                placement,
                since=since,
                until=reported_at + MARGIN,
                limit=LOG_LINES,
                needle=needle,
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


LOGS = ToolsetSpec(
    name="backend.logs",
    description=(
        "Search the case's service log where it is kept — Loki for "
        "production, kubectl for dev: read_log."
    ),
    factory=lambda run: log_tools(run, log_sources(run)),
    mcp={LOKI_SERVER: LokiSource.TOOLS},
    domain_type=Placement,
)
