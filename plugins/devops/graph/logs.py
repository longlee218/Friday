"""Node 2: which window to read, and what to keep of it.

The reading itself is `plugins/devops/sources/logs.py` — two back ends and only two
(D4). This is the formula over them: the window measured back from the
reporter's message, one widening when it holds nothing loud, and the
recall-first cut.

**A source that is not configured skips**, with a reason, and the graph goes
on. That is the shape the deleted five-node graph got wrong — every node of
it skipped on every run and nothing said so out loud — so the skip is an
envelope the board renders and `Diagnose` reads as `not_checked`, not a
silent empty list.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from plugins.devops.graph.distil import distil, frames as frames_of
from plugins.devops.graph.resolve import path_of, resolved
from plugins.devops.graph.deps import ApiIssueDeps
from friday.sdk.workflow import Ask, DAGState, Node, envelope
from friday.sdk.sources import Lines

__all__ = ["dossier_of", "find_request_log_node"]

log = logging.getLogger(__name__)

#: **The window is measured back from the reporter's message, not from now**
#: (D5: "path plus identifier within a window of six hours back from the
#: reporter's message"). A clock anchored to now finds nothing for a case
#: reported this morning, and every one of ticket 00's five cases is old —
#: the slice would have been unrunnable against the thing it exists to run
#: against.
FIRST_WINDOW = timedelta(minutes=30)
WIDER_WINDOW = timedelta(hours=6)

#: How far *past* the message to read. A log line is written before the
#: person complains about it, but not always before their clock says so.
MARGIN = timedelta(minutes=5)

FIRST_LINES = 400
WIDER_LINES = 2000

#: How many of *other* requests' error lines are worth quoting beside the
#: counts. The spec's own number, and the reason there is a histogram: the
#: first real run quoted sixty-one and called it a dossier.
OTHER_ERRORS = 8


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
        return datetime.now(timezone.utc)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def _said(window: timedelta) -> str:
    hours, seconds = divmod(int(window.total_seconds()), 3600)
    return f"{hours}h" if hours and not seconds else f"{seconds // 60}m"


def _when(at: datetime) -> str:
    return at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def dossier_of(result: Any) -> tuple[str, tuple[str, ...]]:
    """Node 2's envelope read back: the dossier text, and what it did not
    check."""
    return result.get("dossier", ""), tuple(result.get("not_checked", ()))


def histogram_of(result: Any) -> tuple[tuple[str, int], ...]:
    """The window's error codes and their counts, as `Diagnose` is shown
    them."""
    if not isinstance(result, dict):
        return ()
    return tuple((str(code), int(n)) for code, n in result.get("histogram", ()))


def _merged(window: Lines, ours: Lines) -> list[str]:
    """The window read and the narrowed read, as one list for the cut.

    The window first, this request's lines after it, and nothing twice. The
    order is deliberate rather than incidental: `distil` keeps a couple of
    lines either side of each line it keeps, so what a line's neighbours are
    decides what context it is given. Putting this request's lines together
    at the end makes their neighbours *each other* — which is the only
    grouping that means anything, since the two reads cover different spans
    and there is no shared clock left on the lines to interleave them by.

    Deduplicated because the two reads overlap whenever the window was not
    truncated: the same line arriving twice would be counted twice in the
    histogram and quoted twice in the dossier.

    **The seam is marked**, because putting the two blocks end to end makes
    them look contiguous and they are not: in the case this was built for,
    the join is a twenty-three minute jump, and `distil` keeps a line or two
    either side of everything it keeps — so without a marker it reaches
    across that jump and presents two unrelated spans as one story. The
    marker carries no error word, no error code and nothing shaped like a
    stack frame, so it is inert to every rule that reads these lines and
    visible to the one reader that matters.
    """
    seen = set(window.lines)
    mine = [line for line in ours.lines if line not in seen]
    if not mine or not window.lines:
        return [*window.lines, *mine]
    return [
        *window.lines,
        "--- above: the window read. below: a separate read narrowed to "
        "this request, from elsewhere in the same window ---",
        *mine,
    ]


def _covered(read: Lines) -> str:
    """The span a read actually handed over, for a sentence about what it
    left out. A back end that would not say gets said so, rather than an
    invented span or a silence that reads as the whole window."""
    if read.oldest is None or read.newest is None:
        return "an unknown part"
    return f"{_when(read.oldest)}–{_when(read.newest)}"


def _matching(
    needles: tuple[str, ...], correlation_id: str | None, lines: list[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """What to match on besides the correlationId, and what to say about it.

    The endpoint path, but only when the correlationId did not find the
    request.

    D2 says "a curl, **or** an endpoint plus one identifier the log line
    carries" — *or*. The path is the fallback for a report that has no
    correlationId, and passing both costs exactly what the spec's line cap
    is there to prevent: measured on the production case, 2026-09-21, the
    dossier is **18 lines with the path and 8 without**, and all ten extra
    lines are *other people's successful calls to the same endpoint*, two
    lines of context each. The request's own lines are the same two either
    way.

    Still a fallback rather than a deletion: a correlationId that the log
    does not carry — the reporter read it off a response body, a gateway
    rewrote it — would otherwise take the path down with it and leave a
    dossier of nothing. So the test is whether it actually matched, not
    whether it was supplied.

    **And it says so when it falls back.** "The correlationId you gave
    appears in no line here" is close to the most actionable thing a dossier
    can carry: it means the id is wrong, or the service is not the one that
    logged it, or the window is. Detecting that and then discarding the
    detection — which the first version of this did — turns a finding into a
    dossier that quietly widened its own search.
    """
    # **One needle, the narrowest that actually matched.** `distil` ORs what
    # it is given, so handing it the endpoint *as well as* the identifier is
    # handing it the endpoint: measured on this shape, a window of eight
    # other callers plus this reporter distils to 9 lines with both and 3
    # with the identifier alone. The identifier names this reporter; the
    # endpoint names everyone who ever called it.
    def carried(needle: str | None) -> bool:
        return bool(needle) and any(needle in line for line in lines)

    # **Said whatever else matched.** Computed before the choice rather than
    # inside it: an endpoint that happens to match is not a reason to stop
    # reporting that the id did not, and folding the two together dropped
    # this sentence the first time it was written.
    unmatched = (
        (
            f"no line in what was read carries the correlationId "
            f"{correlation_id!r}, so the request was matched by "
            + (f"{needles[0]!r}" if needles else "nothing narrower")
            + " instead — which can match callers other than this one",
        )
        if correlation_id and not carried(correlation_id)
        else ()
    )

    # Ordered narrowest first, and the first that appears in what was read
    # wins. `correlation_id` needs no entry in `matching` because `distil`
    # takes it as its own argument.
    for candidate in (correlation_id, *needles):
        if carried(candidate):
            return ((), unmatched) if candidate == correlation_id else (
                (candidate,), unmatched
            )
    # Nothing named this request at all. The widest is all there is, and the
    # dossier says "nothing named this request" for itself.
    return needles[-1:], unmatched


def _unanswered(params: Any, correlation_id: str | None, needle: str) -> str:
    """What is worth asking the reporter, or `""` when nothing is.

    `""` matters as much as the question: a reporter who already pasted a
    response *and* gave a correlationId that the log simply does not carry
    has told us everything they have, and asking again is how a system
    teaches people to stop answering it. That case gets the envelope and
    the honest "searched and not found" instead.
    """
    if not getattr(params, "response", None):
        return (
            "the response you got back — it carries the id this is searched "
            "for — and roughly when it happened"
        )
    if not needle:
        return (
            "the endpoint you called and one id the request carried "
            "(deviceId, userId, email or order id), and roughly when"
        )
    return ""


def _capped(
    wanted: str, window: Lines, ours: Lines, *, needle: str, asked: timedelta
) -> tuple[str, ...]:
    """What each read left out, said in the reader's own terms.

    **Which part of the window, not merely "a sample".** The first version
    of this sentence said a sample and stopped, and a dossier drawn from the
    last eighty-four seconds of a thirty-five minute window reads exactly
    like a dossier of the whole of it. Naming the span makes the difference
    legible — and says which claims it limits, which is the counts and other
    requests' lines, never this request's own: those came from a read
    narrowed to them.

    Out of the node because it is the node's least readable paragraph and
    none of it depends on the run: two reads, what was asked for, and the
    sentences that follow.
    """
    said: list[str] = []
    if window.truncated:
        limits = (
            f"{wanted} capped the window read at {_covered(window)} of the "
            f"{_said(asked)} asked for, so the error-code counts and any "
            f"line belonging to another request cover that part of the "
            f"window and this request's own lines, not the rest of the window"
        )
        if needle:
            limits += (
                "; this request's own lines came from a separate read "
                "narrowed to it and are not a sample"
            )
        said.append(limits)
    if ours.truncated:
        # A different shape of incompleteness, and worth its own sentence:
        # the dossier is a prefix of one request rather than a sample of a
        # window.
        said.append(
            f"{wanted} capped even the search for {needle!r}, so this "
            f"request has more lines than were read"
        )
    return tuple(said)


def find_request_log_node(*, timeout_seconds: float | None = None) -> Node:
    """Build node 2.

    `deps.log_sources` is how the sources arrive — built once at boot from the
    configuration by `api_issue`'s deps factory (ticket 13), the same way the
    servers are, and handed to this run as a typed field.
    """

    async def _find(state: DAGState, deps: ApiIssueDeps) -> Any:
        placement, _ = resolved(state["resolve"])
        # On the first run, the params `prepare` extracted. On a re-run after an
        # `Ask`, the params the pool re-extracted from the reporter's answer and
        # delivered here (model B: the answer re-runs this node with what it now
        # knows, in place of v1's re-run from node 0). The happy path — a
        # dossier found without asking — never touches `deps.answers`, so the
        # replay eval is unchanged.
        params = deps.answers[-1] if deps.answers else state["prepare"]
        sources: dict[str, Any] = deps.log_sources
        wanted = "loki" if placement.env == "production" else "kubectl"
        source = sources.get(wanted)
        if source is None:
            return envelope(
                "skipped",
                f"no {wanted} source is configured, so {placement.env} logs "
                "were not read at all",
            )

        # D2's two ways of naming a request, in the order the type now offers
        # them (board `read-it-the-way-the-operator-does`, ticket 01): the
        # path off the curl, or — for the reporter who wrote "login API,
        # deviceId X, 500" and pasted nothing — the endpoint they named, plus
        # the one id they gave.
        endpoint = path_of(getattr(params, "curl", None)) or getattr(
            params, "endpoint", None
        )
        identifier = getattr(params, "identifier", None)
        # **Narrowest first**, which is what `_matching` walks: the
        # identifier names this reporter, the endpoint names everyone who
        # ever called it.
        needles = tuple(n for n in (identifier, endpoint) if n)
        correlation_id = getattr(params, "correlation_id", None)
        reported_at = _reported_at(deps.task)

        # **The one string the source can search for.** The correlationId
        # when the response the reporter pasted carried one; then the
        # identifier; then the endpoint. All three are plain substrings of
        # the log line, which is what both back ends can narrow on without
        # being told the log's format.
        #
        # The identifier before the endpoint, because the endpoint matches
        # every caller of it and the identifier matches this reporter:
        # measured on the production case, 2026-09-21, narrowing on the path
        # returned 18 lines where ten were other people's successful calls.
        # A read narrowed to the wrong thing is worse than the wide one,
        # because `limit` is a tail and the ten crowd out this request.
        needle = correlation_id or identifier or endpoint or ""
        until = reported_at + MARGIN

        async def read(since: datetime, limit: int) -> tuple[Lines, Lines]:
            """The window, and — when anything names this request — that
            request's own lines, fetched separately.

            **Two reads because one cannot be both.** `limit` is a tail: a
            thirty-five minute window of `backend-reelme-v2` is ~12,400 lines
            and `limit=400` returns the newest eighty-four seconds of it
            (measured, 2026-09-21). The request under investigation was
            twenty-three minutes outside that, so the first read is narrowed
            to the request and cannot miss it, and the second is the window
            as before — kept because the error-code histogram counts the
            window, and a histogram of the request's own lines counts one
            thing once.
            """
            window = await source.lines(
                placement, since=since, until=until, limit=limit
            )
            if not needle:
                return window, Lines()
            return window, await source.lines(
                placement, since=since, until=until, limit=limit, needle=needle,
            )

        since = reported_at - FIRST_WINDOW
        try:
            window, ours = await read(since, FIRST_LINES)
        except Exception as exc:  # noqa: BLE001 — a source that is down is work
            return envelope(
                "error", f"{wanted} could not be read: {type(exc).__name__}: {exc}"
            )

        merged = _merged(window, ours)
        matching, unmatched = _matching(needles, correlation_id, merged)
        dossier = distil(
            merged, correlation_id=correlation_id, matching=matching,
            max_lines=FIRST_LINES, other_error_cap=OTHER_ERRORS,
        )
        widened = ()
        if dossier.worth_widening:
            # One widening, and only one (spec). A window that finds nothing
            # loud twice is a window that is not the problem.
            since = reported_at - WIDER_WINDOW
            try:
                window, ours = await read(since, WIDER_LINES)
            except Exception as exc:  # noqa: BLE001
                return envelope(
                    "error",
                    f"{wanted} could not be read on the wider window: "
                    f"{type(exc).__name__}: {exc}",
                )
            merged = _merged(window, ours)
            matching, unmatched = _matching(needles, correlation_id, merged)
            dossier = distil(
                merged, correlation_id=correlation_id, matching=matching,
                max_lines=WIDER_LINES, other_error_cap=OTHER_ERRORS,
            )
            widened = (
                f"the {_said(FIRST_WINDOW)} before {_when(reported_at)} "
                f"held no error and no stack, so the window was widened once "
                f"to {_said(WIDER_WINDOW)}",
            )

        # **Out of reach is not the same as not found**, and the first real
        # run needed the difference. Case 1 was reported sixteen hours before
        # the oldest line its pod still held: every line the window could
        # have held was gone, and saying "nothing names this request" would
        # send the operator looking for a request that was never searched.
        out_of_reach = (
            window.oldest is not None
            and not window.lines
            and not ours.lines
            and window.oldest > since
        )
        if out_of_reach:
            return envelope(
                "empty",
                f"{wanted} holds nothing from {_when(since)}: its oldest line "
                f"is {_when(window.oldest)}. On dev that is the pod's last "
                f"restart, so this request was not searched for — it is no "
                f"longer there to search.",
                dossier="",
                source=wanted,
                total=0,
                not_checked=[*widened, "the log does not reach back to the report"],
            )

        capped = _capped(wanted, window, ours, needle=needle, asked=until - since)
        not_checked = (*widened, *unmatched, *capped, *dossier.not_checked)
        if not dossier.lines:
            # **The window was searched and this request is not in it.** Not
            # the same as the retention case above, which returned before
            # here: there, every line the window could have held was already
            # gone, and asking a reporter to re-send something into a log
            # that no longer reaches back is asking for nothing.
            #
            # Here the log *does* reach back, so the likeliest reasons are
            # answerable by the reporter: the id was read off something else,
            # the endpoint was named loosely, or it happened outside the
            # window measured from their message. D2 asks for the response —
            # which is where a correlationId actually comes from (ticket 01)
            # — and for when it happened.
            #
            # An `Ask` rather than an envelope, so the pool queues the
            # question, moves the task to waiting, and re-enters this node
            # when the answer arrives: the node did not finish, so the
            # checkpoint does not carry it and the resumed run searches
            # again with what it now knows. The bound on asking the same
            # task over and over is the pool's, not this node's.
            missing = _unanswered(params, correlation_id, needle)
            if missing:
                return Ask(missing)
            return envelope(
                "empty",
                f"{wanted} returned {dossier.total} lines in the window and "
                f"none of them names this request",
                dossier="",
                source=wanted,
                total=dossier.total,
                not_checked=list(not_checked),
            )
        return envelope(
            "ok",
            "",
            dossier=dossier.text(),
            source=wanted,
            total=dossier.total,
            kept=dossier.kept,
            has_stack=dossier.has_stack,
            histogram=[[code, n] for code, n in dossier.histogram],
            # Read here rather than by the code node, off the `Dossier` that
            # is still an object — the envelope carries text, and a second
            # pattern re-finding frames in it is a second pattern to keep in
            # step with `has_stack`.
            frames=[[file, line] for file, line in frames_of(dossier)],
            not_checked=list(not_checked),
        )

    return Node("find_request_log", _find, timeout_seconds=timeout_seconds)  # type: ignore[arg-type]  # ApiIssueDeps subtype; see acknowledge.py
