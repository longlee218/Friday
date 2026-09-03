"""The first real graph: what to do about an API that is behaving wrongly.

```
prepare → read_logs → find_code_path → analyze_stack ─┬→ fix_bug → compose_reply
                                                       └→ compose_reply
```

Every node degrades rather than fails. A node whose tool server is not
configured returns `None` and costs nothing — no model call, no error. The
graph still reaches `compose_reply`, which hands over: the report was
traceable enough to get here, so nothing found means the investigation came
up empty, not that the reporter left something out.

That degradation is the point of shipping it this way. Replacing a working
planner with a graph that only works once Loki is wired would be a regression
dressed as progress.

Whether a report is traceable at all is decided by `prepare`, the entry node —
`_traceable` in `ApiIssueParams._RULES` is one of the rules it checks. Nothing
past `prepare` ever sees a report that fails it.

Only `analyze_stack` and `compose_reply` call a model. The other three are
tool work. That ratio is why the graph is worth having: it gives us
somewhere to *skip* the expensive step.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from friday.agent.harness import ToolContext, tool
from friday.dag import DAG, DAGDeps, DAGState, Edge, Node
from friday.dag.prepare import prepare_node, prepared_ok
from friday.domain.actions import Action, HandOver, Reply
from friday.domain.models import ApiIssueParams

__all__ = ["ComposeCapture", "build_api_issue_dag"]

log = logging.getLogger(__name__)


# --- what compose_reply's agent reports through -----------------------------


@dataclass
class ComposeCapture:
    """Per-run scratch space for `compose_reply`'s tools — the same pattern
    as triage's and the extractor's: the tool writes here rather than to a
    module global, so concurrent runs cannot overwrite each other."""

    action: Action | None = None


@tool
def answer(ctx: ToolContext[ComposeCapture], text: str) -> str:
    """The reply to send. Room register, examples of the operator's voice and
    any skill you fetched already shaped what you were told to say — write
    the message itself, nothing more.

    Args:
        text: the reply, ready to go out once approved.
    """
    ctx.context.action = Reply(text)
    return "recorded"


@tool
def hand_over(ctx: ToolContext[ComposeCapture], reason: str) -> str:
    """Stop here instead of composing a reply. Quote your own finding — the
    operator reads it directly; a reporter never does.

    Args:
        reason: what you found, in your own words.
    """
    ctx.context.action = HandOver(reason)
    return "recorded"


#: Both tools compose_reply's agent gets. One list so the graph and the
#: agent-building code name the same two things once each.
COMPOSE_TOOLS = [answer, hand_over]


@tool(needs_approval=True)
def apply_fix(diff: str) -> str:
    """Propose this diff as the fix. Only call this once you are sure — the
    operator sees exactly this diff before it reaches anyone.

    Args:
        diff: the change, as a unified diff, ready to be read as-is.
    """
    return diff


#: fix_bug's own two tools — `apply_fix` (ticket 07, needs approval) beside
#: `hand_over` (ticket 06, no approval needed: refusing to act is not the
#: dangerous half). Kept apart from `COMPOSE_TOOLS`: compose_reply never
#: applies anything, and fix_bug never answers a reporter.
FIX_TOOLS = [apply_fix, hand_over]

#: Which tool server each node needs. A node whose server is absent skips.
LOKI = "loki"
SOURCE = "source"

#: Node -> the server it cannot work without. Read twice, from here both
#: times: the node checks it before spending a model call, and
#: `dag/router.py` reads it to hand the agent the server it will look for.
#: Stated in two files, those two would drift and nothing would catch it.
NODE_SERVERS = {
    "read_logs": LOKI,
    "find_code_path": SOURCE,
    "fix_bug": SOURCE,
}


# --- the nodes -------------------------------------------------------------


def _params(state: DAGState) -> ApiIssueParams:
    """The parameters as `prepare` left them this pass — not `deps.task.params`,
    which is a snapshot from before this pass's extraction ran."""
    result = state["prepare"]
    assert isinstance(result, ApiIssueParams), "reached with prepare unresolved"
    return result


async def _read_logs(state: DAGState, deps: DAGDeps) -> str | None:
    """Pull the log lines for this request, if there is anything to pull with."""
    params = _params(state)
    if not (params.correlation_id or params.curl):
        return None  # nothing to look up by
    agent = deps.extra.get("read_logs")
    if agent is None or NODE_SERVERS["read_logs"] not in deps.servers:
        log.debug("api_issue: no log server configured, skipping read_logs")
        return None

    result = await agent.run(
        f"correlation id: {params.correlation_id}\n"
        f"environment: {params.environment or 'unknown'}"
    )
    if result is None:
        return None
    lines = (result.final_output or "").strip()
    return None if lines == "NO LOGS" else lines or None


async def _find_code_path(state: DAGState, deps: DAGDeps) -> str | None:
    """Locate the code the stack trace points at."""
    logs = state.get("read_logs")
    if not isinstance(logs, str) or not logs:
        return None
    agent = deps.extra.get("find_code_path")
    if agent is None or NODE_SERVERS["find_code_path"] not in deps.servers:
        return None

    result = await agent.run(logs)
    if result is None:
        return None
    found = (result.final_output or "").strip()
    return None if found == "NOT FOUND" else found or None


async def _analyze_stack(state: DAGState, deps: DAGDeps) -> dict[str, Any]:
    """Say why it failed, and whether that is certain enough to act on.

    Always returns the same shape, so the edge predicate downstream has one
    thing to read rather than three cases to handle.
    """
    logs = state.get("read_logs")
    if not isinstance(logs, str) or not logs:
        return {"cause": None, "actionable": False, "evidence": []}

    agent = deps.extra.get("analyze_stack")
    if agent is None:
        return {"cause": None, "actionable": False, "evidence": []}

    code = state.get("find_code_path") or "(the code could not be located)"
    # Two extra turns: this node may be offered `fetch_skill`, and the call
    # plus its answer both land before the analysis is written. `max_turns` is
    # a ceiling, not a budget — a node with no tool still finishes in one.
    result = await agent.run(f"logs:\n{logs}\n\ncode:\n{code}", extra_turns=2)
    if result is None:
        return {"cause": None, "actionable": False, "evidence": []}

    return _as_analysis(result.final_output or "")


#: Words that mean a change is not ours to make unattended. Matched against
#: the cause *and* against the path the fix would touch: a cause reading
#: "off-by-one in the loop bound" says nothing about the file it is in, and
#: the file was `migrations/versions/443468757024_baseline_schema.py`.
_HANDS_OFF = ("migration", "schema", "credential", "secret", "password", "token")


async def _fix_bug(state: DAGState, deps: DAGDeps) -> str | HandOver | None:
    """Apply the fix, or stop and ask.

    Reached only when `analyze_stack` said the cause is actionable. Even then
    there are changes this should not make on its own, and the honest move is
    to stop rather than to widen what "actionable" was allowed to mean.
    Returning a `HandOver` ends the run right here, the same as any node deciding
    what to send — `PauseForHuman` used to be a second way to do that, raised
    instead of returned; ticket 03 made "new text re-runs from node 1" do
    everything "resume from the paused node" did, so there was nothing left
    for the second mechanism to buy.
    """
    analysis = state["analyze_stack"]
    cause = (analysis.get("cause") or "") if isinstance(analysis, dict) else ""
    where = str(state.get("find_code_path") or "")

    subject = f"{cause}\n{where}".lower()
    touched = [word for word in _HANDS_OFF if word in subject]
    if touched:
        return HandOver(
            f"The cause mentions {touched[0]}, so I have not changed "
            f"anything. Cause: {cause} (I'll handle it / go ahead and fix it)"
        )

    agent = deps.extra.get("fix_bug")
    if agent is None or NODE_SERVERS["fix_bug"] not in deps.servers:
        return HandOver(
            f"I found the cause but cannot change code from here. Cause: {cause}"
        )

    # `hand_over` here too: the prompt asks this agent to refuse when the fix
    # is not obvious or touches more than it should, and a refusal in prose
    # used to be read as the diff itself — the code never checked for the
    # `CANNOT FIX` sentinel it asked the model to write, so a model that
    # refused correctly still had its refusal proposed as a patch.
    capture = ComposeCapture()
    result = await agent.run(
        f"cause: {cause}\ncode: {state.get('find_code_path')}",
        context=capture,
        extra_turns=2,
    )
    if result is not None and result.interruptions:
        # `apply_fix` wants to run — D15's gate. The run stops holding its
        # own state; `_run_dag` checkpoints it alongside this node, and
        # `decide_pending_action` is what resumes or declines it. Nothing
        # here decides which; that is the operator's call, not this node's.
        return HandOver(
            f"Found a fix. It needs approval before I use it. Cause: {cause}",
            interruption=agent.checkpoint(result),
        )
    if capture.action is not None:
        return capture.action
    return (result.final_output or "").strip() if result else None


def _fix_bug_ok(state: DAGState) -> bool:
    """Whether `fix_bug` decided to continue rather than stop the run here."""
    return not isinstance(state.get("fix_bug"), HandOver)


async def _compose_reply(state: DAGState, deps: DAGDeps) -> Action:
    """Decide what actually goes back, and in what form.

    This is the node that produces the graph's `Action`, and the only one that
    knows the difference between "we found something worth saying" and "we
    need something from them first". Its agent reports which by calling
    `answer` or `hand_over` (ticket 06) — nothing here parses its prose
    looking for one. A sentinel was a private protocol between the prompt and
    this function, and a model that wandered off it — once, wearing a
    Markdown code fence — had its prose read as the reply and proposed under
    the operator's name.

    With no agent configured it hands over instead of answering. It used to
    reply — `Reply(f"{cause}\n\n{fix}")`, reproducing the deterministic
    planner this graph replaced — and that was the planner's bug carried
    forward, not a feature worth preserving (ticket 10): `cause` is
    `analyze_stack`'s own sentence and `fix` is `fix_bug`'s raw unified diff,
    both Node family, and a `Reply` is queued under the operator's name and
    sent to whoever reported the bug. So the one configuration the docs call
    the ordinary degraded mode was the one that broke the invariant — only
    Responder-family agents produce text a reporter reads — and posted an
    unreviewed patch to them besides. Handing over puts the same two things
    in front of the operator, which is who they were always for.
    """
    params = _params(state)
    analysis = state.get("analyze_stack") or {}
    cause = analysis.get("cause") if isinstance(analysis, dict) else None

    if cause:
        fix = state.get("fix_bug")
        said = f"{cause}" if not fix else f"{cause}\n\n{fix}"
        agent = deps.extra.get("compose_reply")
        if agent is not None:
            # Same family as the responder, same room. The register of the
            # channel this goes back into is part of what to say.
            from friday.agent.instruction_prompt import channel_sections

            store = deps.extra.get("context_store")
            room = store.context(deps.task.conversation.channel_id) if store else None
            prompt = "\n".join(p for p in (channel_sections(room), said) if p)
            capture = ComposeCapture()
            written = await agent.run(prompt, context=capture, extra_turns=2)
            if written is not None and capture.action is not None:
                return capture.action
        # No agent, or one that answered nothing: the investigation found
        # something and no Responder-family agent is here to say it. The
        # operator reads `said` as a finding — the one audience Node-family
        # prose and a diff were ever meant for.
        return HandOver(f"Found something, but nobody wrote a reply: {said}")

    # Nothing found — and by the time this runs, that no longer means "we were
    # never given enough". `_traceable` in `ApiIssueParams._RULES` gates the
    # route: a report with neither a correlationId nor a curl is turned back
    # at `prepare`, the entry node, and never reaches this one. So arriving
    # here with nothing found means the investigation itself came up empty,
    # which is a person's problem, not a question for the reporter.
    #
    # There was an `Ask` here, inherited from the deterministic planner this
    # graph replaced. It became unreachable when the rule moved into the gate,
    # and it worded the same question `_question` and `_ASKED_AS` word — the
    # second copy that drifts.
    return HandOver(
        "traceable, but nothing was found — no log server, or nothing to find"
    )


# --- the graph -------------------------------------------------------------


def _actionable(state: DAGState) -> bool:
    """Whether to attempt a fix at all.

    A cause is required, not merely the `actionable` flag. `{"actionable":
    true, "cause": null}` is a shape `_as_analysis` produces from a model that
    answered half the question, and it used to disarm the guard completely:
    `_fix_bug` matches `_HANDS_OFF` against the cause, an empty cause matches
    nothing, and the fixer was handed a migration to patch with no stated
    reason. `_compose_reply` then saw a falsy cause and dropped the diff on
    the floor, so the change was made and never mentioned.
    """
    analysis = state.get("analyze_stack")
    if not isinstance(analysis, dict):
        return False
    return bool(analysis.get("actionable") and analysis.get("cause"))


def build_api_issue_dag() -> DAG:
    """The graph. Agents are handed in through `deps`, not closed over here,
    so the shape can be tested without a model or a tool server."""
    return DAG(
        name="api_issue",
        nodes=(
            prepare_node("api_issue", ApiIssueParams),
            Node("read_logs", _read_logs),
            Node("find_code_path", _find_code_path),
            Node("analyze_stack", _analyze_stack),
            Node("fix_bug", _fix_bug),
            Node("compose_reply", _compose_reply),
        ),
        edges=(
            Edge("prepare", "read_logs", when=prepared_ok),
            Edge("read_logs", "find_code_path"),
            Edge("find_code_path", "analyze_stack"),
            Edge("analyze_stack", "fix_bug", when=_actionable),
            Edge("analyze_stack", "compose_reply"),
            Edge("fix_bug", "compose_reply", when=_fix_bug_ok),
        ),
    )


def _as_analysis(text: str) -> dict[str, Any]:
    """Read the analyst's answer, however it came back.

    JSON when it obeys, a bare sentence when it does not. A model that
    ignored the format is still telling us something; `actionable` stays
    false because a shape we did not ask for is not evidence of certainty.
    """
    import json

    stripped = _unfence(text.strip())
    if stripped.startswith("{"):
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(data, dict):
                return {
                    "cause": data.get("cause"),
                    "actionable": _is_yes(data.get("actionable")),
                    "evidence": data.get("evidence") or [],
                }
    return {"cause": stripped or None, "actionable": False, "evidence": []}


def _unfence(text: str) -> str:
    """Strip a Markdown code fence, if the answer arrived wearing one.

    ```json {...} ``` is the most ordinary shape a model returns JSON in, and
    without this the whole blob failed the `startswith("{")` check, became the
    `cause` verbatim, and lost a genuine `actionable: true` on the way — so
    the fix edge was never taken and the fenced text was proposed as the reply
    to send under the operator's name.
    """
    if not text.startswith("```"):
        return text
    body = text[3:]
    #: ```json / ```JSON / ``` — the language tag, if there is one.
    if "\n" in body:
        first, _, rest = body.partition("\n")
        if not first.strip() or first.strip().isalpha():
            body = rest
    closing = body.rfind("```")
    return (body[:closing] if closing != -1 else body).strip()


def _is_yes(value: Any) -> bool:
    """Whether the analyst actually said yes.

    `bool("false")` is `True`, and a model asked for JSON returns the string
    "false" often enough that taking the truthiness would read a refusal as
    permission. The prompt says a wrong "true" here spends a code change on a
    guess; this is the line where that would have happened.

    Anything that is not recognisably a yes is a no. Uncertainty resolves
    towards not touching the code.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "y", "1"}
    return False
