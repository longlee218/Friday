"""The first real graph: what to do about an API that is behaving wrongly.

```
read_logs → find_code_path → analyze_stack ─┬→ fix_bug → compose_reply
                                            └→ compose_reply
```

Every node degrades rather than fails. A node whose tool server is not
configured returns `None` and costs nothing — no model call, no error. The
graph still reaches `compose_reply`, which parks: the report was traceable
enough to get here, so nothing found means the investigation came up empty,
not that the reporter left something out.

That degradation is the point of shipping it this way. Replacing a working
planner with a graph that only works once Loki is wired would be a regression
dressed as progress.

Whether a report is traceable at all is decided before any of this, by
`_traceable` in `ApiIssueParams._RULES`. This graph never sees one that is
not.

Only `analyze_stack` and `compose_reply` call a model. The other three are
tool work. That ratio is why the graph is worth having: it gives us
somewhere to *skip* the expensive step.
"""

from __future__ import annotations

import logging
from typing import Any

from friday.dag import DAG, DAGDeps, DAGState, Edge, Node
from friday.dag.pause import PauseForHuman
from friday.domain.actions import Action, Ask, Park, Reply
from friday.domain.models import ApiIssueParams

__all__ = ["build_api_issue_dag"]

log = logging.getLogger(__name__)

#: Which tool server each node needs. A node whose server is absent skips.
LOKI = "loki"
SOURCE = "source"

#: Node -> the server it cannot work without. Read twice, from here both
#: times: the node checks it before spending a model call, and
#: `dag/workflows.py` reads it to hand the agent the server it will look for.
#: Stated in two files, those two would drift and nothing would catch it.
NODE_SERVERS = {
    "read_logs": LOKI,
    "find_code_path": SOURCE,
    "fix_bug": SOURCE,
}


# --- the nodes -------------------------------------------------------------


def _params(deps: DAGDeps) -> ApiIssueParams:
    """The task's parameters, as the type that declares what they mean."""
    return ApiIssueParams(**deps.task.params)


async def _read_logs(state: DAGState, deps: DAGDeps) -> str | None:
    """Pull the log lines for this request, if there is anything to pull with."""
    params = _params(deps)
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


async def _fix_bug(state: DAGState, deps: DAGDeps) -> str | None:
    """Apply the fix, or stop and ask.

    Reached only when `analyze_stack` said the cause is actionable. Even then
    there are changes this should not make on its own, and the honest move is
    to ask rather than to widen what "actionable" was allowed to mean.
    """
    analysis = state["analyze_stack"]
    cause = (analysis.get("cause") or "") if isinstance(analysis, dict) else ""
    where = str(state.get("find_code_path") or "")

    subject = f"{cause}\n{where}".lower()
    touched = [word for word in _HANDS_OFF if word in subject]
    if touched:
        raise PauseForHuman(
            question=(
                f"The cause mentions {touched[0]}, so I have not changed "
                f"anything. Cause: {cause}"
            ),
            options=["I'll handle it", "go ahead and fix it"],
            evidence={"cause": cause, "code": state.get("find_code_path")},
            node="fix_bug",
        )

    agent = deps.extra.get("fix_bug")
    if agent is None or NODE_SERVERS["fix_bug"] not in deps.servers:
        raise PauseForHuman(
            question=(
                f"I found the cause but cannot change code from here. "
                f"Cause: {cause}"
            ),
            evidence={"cause": cause, "code": state.get("find_code_path")},
            node="fix_bug",
        )

    result = await agent.run(
        f"cause: {cause}\ncode: {state.get('find_code_path')}"
    )
    return (result.final_output or "").strip() if result else None


async def _compose_reply(state: DAGState, deps: DAGDeps) -> Action:
    """Decide what actually goes back, and in what form.

    This is the node that produces the graph's `Action`, and the only one that
    knows the difference between "we found something worth saying" and "we
    need something from them first".

    With no tools configured this reproduces the deterministic planner it
    replaced, which is what makes removing that planner safe.
    """
    params = _params(deps)
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
            written = await agent.run(prompt, extra_turns=2)
            if written is not None and (written.final_output or "").strip():
                return Reply(written.final_output.strip())
        return Reply(said)

    # Nothing found — and by the time this runs, that no longer means "we were
    # never given enough". `_traceable` in `ApiIssueParams._RULES` gates the
    # route: a report with neither a correlationId nor a curl is turned back at
    # `prepare()` and never reaches a node. So arriving here with nothing found
    # means the investigation itself came up empty, which is a person's
    # problem, not a question for the reporter.
    #
    # There was an `Ask` here, inherited from the deterministic planner this
    # graph replaced. It became unreachable when the rule moved into the gate,
    # and it worded the same question `_question` and `_ASKED_AS` word — the
    # second copy that drifts.
    return Park(
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
            Node("read_logs", _read_logs),
            Node("find_code_path", _find_code_path),
            Node("analyze_stack", _analyze_stack),
            Node("fix_bug", _fix_bug),
            Node("compose_reply", _compose_reply),
        ),
        edges=(
            Edge("read_logs", "find_code_path"),
            Edge("find_code_path", "analyze_stack"),
            Edge("analyze_stack", "fix_bug", when=_actionable),
            Edge("analyze_stack", "compose_reply"),
            Edge("fix_bug", "compose_reply"),
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
