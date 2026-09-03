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

from friday.agent.harness import Harness
from friday.agent.instruction_prompt import user_input
from friday.tools.fetch_skill import fetch_skill_tool
from friday.dag.api_issue import prompt as prompts
from friday.dag.engine import DAG, DAGDeps, DAGState, Edge, Node, NodeFn
from friday.dag.prepare import prepare_node, prepared_ok
from friday.domain.actions import Action, HandOver, Reply
from friday.tools.patch import FIX_TOOLS
from friday.tools.reply import COMPOSE_TOOLS, ComposeCapture
from friday.domain.models import ApiIssueParams

__all__ = [
    "ComposeCapture",
    "absent_servers",
    "build_agents",
    "build_api_issue_dag",
]

log = logging.getLogger(__name__)


#: Which tool server each node needs. A node whose server is absent skips.
LOKI = "loki"
SOURCE = "source"


def _agent_for(deps: DAGDeps, node: str):
    """This node's agent, or `None` if it cannot work.

    Two conditions, and every node has both: an agent was built for it, and
    the tool server it named is there. The server comes off the same
    declaration the agent builder reads, so the node checking before it
    spends a model call and the builder handing that agent its server cannot
    drift apart. A node that declared no server passes that half by default.

    What each caller does with `None` is its own business — three of them
    return nothing and let the next node see absence, one hands over.
    """
    agent = deps.extra.get(node)
    if agent is None:
        return None
    server = NODES[node].server
    return agent if server is None or server in deps.servers else None


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
    agent = _agent_for(deps, "read_logs")
    if agent is None:
        log.debug("api_issue: no agent or no log server, skipping read_logs")
        return None

    result = await agent.run(
        user_input(
            f"correlation id: {params.correlation_id}\n"
            f"environment: {params.environment or 'unknown'}"
        )
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
    agent = _agent_for(deps, "find_code_path")
    if agent is None:
        return None

    result = await agent.run(user_input(logs))
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

    agent = _agent_for(deps, "analyze_stack")
    if agent is None:
        return {"cause": None, "actionable": False, "evidence": []}

    code = state.get("find_code_path") or "(the code could not be located)"
    # Two extra turns: this node may be offered `fetch_skill`, and the call
    # plus its answer both land before the analysis is written. `max_turns` is
    # a ceiling, not a budget — a node with no tool still finishes in one.
    result = await agent.run(
        user_input(f"logs:\n{logs}\n\ncode:\n{code}"), extra_turns=2
    )
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

    agent = _agent_for(deps, "fix_bug")
    if agent is None:
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
        user_input(f"cause: {cause}\ncode: {state.get('find_code_path')}"),
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
        agent = _agent_for(deps, "compose_reply")
        if agent is not None:
            # Same family as the responder, same room. The register of the
            # channel this goes back into is part of what to say.
            from friday.agent.instruction_prompt import channel_sections

            store = deps.extra.get("context_store")
            room = store.context(deps.task.conversation.channel_id) if store else None
            prompt = "\n".join(
                p for p in (channel_sections(room), user_input(said)) if p
            )
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


@dataclass(frozen=True, slots=True)
class _Node:
    """Everything that defines one node of this graph, declared once.

    Six things used to be keyed by these same five names, across three files:
    node to server, node to configuration block, node to prompt, which nodes
    reason, which nodes get which tools, and which persona family each is
    built with. One of those six sat beside a comment arguing for exactly the
    rule the other five broke. This is that comment's rule applied to all of
    them (ticket 15).

    Two things are projected out of it and nothing else reads it: the node the
    engine walks (`build_api_issue_dag`) and the agent behind that node
    (`build_agents`). The engine still receives a name and a function, and
    still knows nothing about agents, configuration or prompts.
    """

    #: The function the engine runs — the engine's own type, since this is
    #: what `build_api_issue_dag` hands it. Every node has one; not every node
    #: has an agent, and `read_logs` with no log server still runs and skips.
    run: NodeFn
    #: Which `config.agents` block builds this node's agent. A node whose
    #: block is absent runs without one, which every node here survives.
    block: str
    #: What that agent is told, before any per-call input.
    prompt: str
    #: The tool server it cannot work without, if it needs one.
    server: str | None = None
    #: Tools its agent reports through, and the scratch space they write to.
    tools: tuple = ()
    context: type | None = None
    #: Whether it decides what evidence *means*, as opposed to fetching it.
    #: "How to trace a request" is written for whoever reads the logs, not for
    #: the thing that fetches them — so only these get the skills catalogue
    #: and the fetch tool.
    reasons: bool = False
    #: Passed to the SDK when its answer arrives as a tool call rather than as
    #: prose, so the run stops at that call instead of taking another turn.
    stop_at_tools: bool = False


#: Every node past `prepare`, in the order the graph walks them. `prepare` is
#: node 0 of every graph and is built by `prepare_node`, not declared here: it
#: has no agent, no prompt and no server of its own.
NODES: dict[str, _Node] = {
    "read_logs": _Node(
        run=_read_logs, block="dag_read_logs", prompt=prompts.READ_LOGS,
        server=LOKI,
    ),
    "find_code_path": _Node(
        run=_find_code_path, block="dag_find_code", prompt=prompts.FIND_CODE_PATH,
        server=SOURCE,
    ),
    "analyze_stack": _Node(
        run=_analyze_stack, block="dag_analyze", prompt=prompts.ANALYZE_STACK,
        reasons=True,
    ),
    # `hand_over` beside `apply_fix` (ticket 06): the prompt asks this agent to
    # refuse when the fix is not obvious, and a refusal written as prose was
    # once proposed as the diff. `apply_fix` is the one tool in this codebase
    # marked `needs_approval` (ticket 07).
    # Not a reasoning node, and that is deliberate: it is handed a cause
    # somebody else decided, and its job is to write the diff or refuse. The
    # skills catalogue is for whoever decides what evidence *means*.
    "fix_bug": _Node(
        run=_fix_bug, block="dag_fix", prompt=prompts.FIX_BUG,
        server=SOURCE,
        tools=tuple(FIX_TOOLS), context=ComposeCapture, stop_at_tools=True,
    ),
    # The node that produces the graph's answer reports it by tool call, never
    # by prose a node function then has to parse. It stops at `answer` and
    # `hand_over` but *not* at `fetch_skill`, which it is also offered:
    # fetching a skill mid-answer must not end the run before the answer.
    "compose_reply": _Node(
        run=_compose_reply, block="dag_compose", prompt=prompts.COMPOSE_REPLY,
        reasons=True,
        tools=tuple(COMPOSE_TOOLS), context=ComposeCapture, stop_at_tools=True,
    ),
}


def build_api_issue_dag() -> DAG:
    """The graph. Agents are handed in through `deps`, not closed over here,
    so the shape can be tested without a model or a tool server."""
    return DAG(
        name="api_issue",
        nodes=(
            prepare_node("api_issue", ApiIssueParams),
            *(Node(name, spec.run) for name, spec in NODES.items()),
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


def build_agents(
    config: Any, skills: Any = None, servers: dict[str, Any] | None = None
) -> dict[str, Any]:
    """One agent per node that has a configuration block.

    Reads the same declaration the graph is built from, so which node gets
    which tools and which server is stated once. Lived in the
    router until ticket 15, where it made the module that maps a task type to
    a graph know the names of one graph's nodes.
    """
    built: dict[str, Any] = {}
    for name, spec in NODES.items():
        agent_config = config.agents.get(spec.block)
        if agent_config is None:
            continue
        wants_skills = skills is not None and spec.reasons
        tools = [fetch_skill_tool(skills)] if wants_skills else []
        tools += list(spec.tools)
        options: dict[str, Any] = {}
        if spec.stop_at_tools:
            options["tool_use_behavior"] = {
                "stop_at_tool_names": [t.name for t in spec.tools]
            }
        available = servers or {}
        mcp = [available[spec.server]] if spec.server in available else []
        built[name] = Harness(
            config=agent_config,
            instructions=prompts.build_instructions(
                spec.prompt,
                reasons=spec.reasons,
                skills=skills if wants_skills else None,
            ),
            tools=tools,
            mcp_servers=mcp,
            context_type=spec.context,
            **options,
        )
    return built


def absent_servers(agents: dict[str, Any], servers: dict[str, Any] | None) -> list[str]:
    """Which servers the built agents wanted and did not get.

    Worth saying out loud at startup: the node skips correctly without one, it
    just skips silently, and "why did read_logs never look anything up" then
    has no answer anywhere.
    """
    return sorted(
        {
            spec.server
            for name, spec in NODES.items()
            if name in agents and spec.server and spec.server not in (servers or {})
        }
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
