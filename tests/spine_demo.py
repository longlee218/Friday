"""A small domain on the spine, for `tests/test_spine_pass.py` and its crash
child (build-the-spine ticket 14).

`demo.trace` reads one log through `demo.reads` and answers `Found`; its
domain type `Where` has the identity `(env, service)`, read off the
reporter's words. The Planner and the agent run on scripted models, so
everything else — the pass, DBOS, the store, the runner, `run_agent`, the
grounding check, `deliver` — is the real thing.

Run as a script it is the crash child: it runs pass 1 of task 1 and dies
inside the agent's first read (`python tests/spine_demo.py <app db> <system db>`).
"""

from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from friday.kernel.config import AgentConfig, TierConfig
from friday.kernel.domain.conversation import ConversationId
from friday.kernel.domain.messages import InboundEvent, MentionType
from friday.kernel.responder import Draft
from friday.kernel.spine.workflow import Spine
from friday.sdk.action import Action, ActionContract, Limits, Recognition
from friday.sdk.agent import AgentSpec, Budget
from friday.sdk.testing import ScriptedModel, function_call
from friday.sdk.toolset import RunContext, ToolsetSpec, tool

ACTION_NAME = "demo.trace"
LOG = ["POST /users 400 email invalid", "POST /users 200 ok"]
CONVERSATION = ConversationId("fake", "watched")
#: An hour ago: the store's own clock (an `Ask` row's time) and the
#: provider's (a message's) must agree on which came first.
REPORTED = datetime.now(UTC).replace(microsecond=0) - timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class Where:
    IDENTITY: ClassVar[tuple[str, ...]] = ("env", "service")

    env: str
    service: str

    def retrieval_keys(self) -> dict:
        return {}


async def enrich(seed, db) -> Where:
    """`staging` anywhere moves the case; the service is the word after
    `service`."""
    text = seed.request_text
    named = re.search(r"service (\w+)", text)
    return Where(
        env="staging" if "staging" in text else "prod",
        service=named.group(1) if named else "users",
    )


@dataclass
class Found:
    cause: str = field(default="", metadata={"doc": "what caused it"})
    refs: list[str] = field(default_factory=list, metadata={"doc": "line ids"})


def check(found: Found, evidence) -> str | None:
    if not evidence.index:
        return "read nothing"
    missing = [r for r in found.refs if r not in evidence.index]
    return f"points at {missing}, never shown" if missing else None


ACTION = Action(
    name=ACTION_NAME,
    recognition=Recognition(means="m", pick_when=("p",)),
    contract=ActionContract(
        allowed_step_types=frozenset({"agent", "ask", "hand_over", "draft"}),
        allowed_agents=frozenset({"demo.diagnose"}),
        allowed_toolsets=frozenset({"demo.reads"}),
        constraints=(),
        approval_policy="a reply waits for approval",
        acceptance_template="a cause",
        limits=Limits(max_replans=2, max_steps=4),
    ),
    acknowledge=lambda context: f"looking at {context.domain.service}",
)
DIAGNOSE = AgentSpec(
    name="demo.diagnose",
    description="Finds the cause.",
    instructions="Read the log, then answer.",
    result=Found,
    tier="flash",
    toolsets=("demo.reads",),
    budget=Budget(max_turns=6, tokens=100_000),
    temperature=0.0,
    check=check,
)
TIER = TierConfig(
    name="flash", api_key="sk-x", base_url="https://example.invalid/v1", model="m"
)
PLANNER = AgentConfig(
    name="planner",
    api_key="sk-x",
    base_url="https://example.invalid/v1",
    model="m",
    max_turns=4,
    tokens=100_000,
)


class Reads:
    """`demo.reads`: one tool, `read_line`, shown through the run's Evidence
    and counted. `crash` kills the process inside the read (the child)."""

    def __init__(self, crash: bool = False) -> None:
        self.calls = 0
        self.crash = crash

    def spec(self) -> ToolsetSpec:
        def factory(run: RunContext) -> list:
            def read_line(n: int) -> str:
                """Read one log line.

                Args:
                    n: which line, from 0.
                """
                if self.crash:
                    os._exit(1)
                self.calls += 1
                return run.evidence.show([LOG[n]])

            return [tool(read_line)]

        return ToolsetSpec(name="demo.reads", description="the log", factory=factory)


def _core(name: str, tool_name: str) -> ToolsetSpec:
    """A stand-in for a core toolset the Planner reads (nothing to find)."""

    def factory(run: RunContext) -> list:
        def look(query: str) -> str:
            """Look something up.

            Args:
                query: what to look up.
            """
            return "nothing"

        return [tool(look, name=tool_name)]

    return ToolsetSpec(name=name, description=name, factory=factory)


class Responder:
    """The `draft` step's responder, scripted: what it was handed, and the
    reply it writes."""

    tone_examples = 0

    def __init__(self, text: str = "email không hợp lệ") -> None:
        self.text = text
        self.given: list[dict] = []

    async def reply(self, **kwargs) -> Draft:
        self.given.append(kwargs)
        return Draft(self.text)


PLAN = {
    "goal": "find the 400",
    "steps": [
        {
            "id": "p1",
            "type": "agent",
            "agent": "demo.diagnose",
            "brief": "find the 400 in the log",
        },
        {"id": "p2", "type": "draft", "reads": ["p1"]},
    ],
}


def planned(*plans: dict) -> ScriptedModel:
    return ScriptedModel(
        [[function_call("answer", p, call_id=f"plan-{i}")] for i, p in enumerate(plans)]
    )


def read(n: int = 0, call: str = "r0") -> list:
    return [function_call("read_line", {"n": n}, call_id=call)]


def found(cause: str = "email invalid", refs=("L1",), call: str = "a1") -> list:
    return [function_call("answer", {"cause": cause, "refs": list(refs)}, call_id=call)]


def asked(question: str = "which user?", call: str = "q1") -> list:
    return [function_call("ask_reporter", {"question": question}, call_id=call)]


def build_spine(db, *, planner, diagnose, reads: Reads, responder=None, auto_ask=True):
    return Spine(
        db=db,
        actions={ACTION_NAME: ACTION},
        agents={"demo.diagnose": DIAGNOSE},
        toolsets={
            "demo.reads": reads.spec(),
            "core.memory": _core("core.memory", "memory_search"),
            "core.skills": _core("core.skills", "search_skills"),
        },
        tiers={"flash": TIER},
        planner=PLANNER,
        enrichers={"demo": enrich},
        responder=responder if responder is not None else Responder(),
        models={"planner": planner, "demo.diagnose": diagnose},
        auto_ask=auto_ask,
    )


async def said(db, task_id: int, message_id: str, text: str, *, minutes: int = 0):
    """A reporter message on the task, `minutes` after the report."""
    event = InboundEvent(
        provider="fake",
        provider_message_id=message_id,
        channel_id="watched",
        thread_id=None,
        author_id="u-reporter",
        author_name="reporter",
        text=text,
        created_at=REPORTED + timedelta(minutes=minutes),
        mention_type=MentionType.DIRECT,
    )
    await db.record_message(event)
    await db.mark_triaged(event, task_id, decision={"type": ACTION_NAME})


async def open_task(db, text: str = "POST /users trả về 400, service users"):
    task = await db.create_task(
        conversation=CONVERSATION,
        type=ACTION_NAME,
        state="pending",
        confidence=0.9,
        params={},
    )
    await said(db, task.id, f"m{task.id}-0", text)
    return task


def _child() -> None:
    import asyncio

    from friday.kernel.dag import adapter
    from friday.kernel.spine.workflow import pass_id, run_pass
    from friday.store.db import Database

    app_db, system_db = sys.argv[1], sys.argv[2]

    async def go() -> None:
        db = await Database.connect(app_db)
        spine = build_spine(
            db,
            planner=planned(PLAN),
            diagnose=ScriptedModel([read(), found()]),
            reads=Reads(crash=True),
        )
        adapter.register_pass(run_pass, spine.steps())
        adapter.launch("friday-spine-test", system_db)
        await adapter.run_pass(1, 1, pass_id(1, 1))
        os._exit(2)  # the read never crashed — fail loudly

    asyncio.run(go())


if __name__ == "__main__":
    # Through the module, not `__main__`: DBOS pickles a step's result, and
    # the parent unpickles `tests.spine_demo.Where`, not `__main__.Where`.
    from tests.spine_demo import _child as child

    child()
