"""Ticket 14 — one place an agent is run.

Everything every agent needs and none of them should restate: the client, the
settings, the logging hooks, the caps, and the rule that a failure becomes work
for a person rather than an exception nobody catches.

Written after the second agent existed, not before. One agent is a hypothetical
seam; building this against triage alone would have been guessing at what
varies.
"""

from __future__ import annotations

from agents import Agent
from agents.models.interface import Model
from agents.testing import ScriptedModel, assistant_message

from friday.config import AgentConfig
from friday.agent.harness import Harness

CONFIG = AgentConfig(
    name="an-agent", api_key="sk-secret", base_url="https://example.invalid/v1",
    model="test-model", max_turns=1, settings={"temperature": 0}, options={},
)


def harness(*steps, **kw) -> Harness:
    return Harness(
        config=CONFIG,
        instructions="do the thing",
        model=ScriptedModel(list(steps)),
        **kw,
    )


async def test_it_runs_an_agent_and_hands_back_the_result():
    result = await harness([assistant_message("done")]).run("go")

    assert result.final_output == "done"


async def test_a_failure_is_no_result_rather_than_an_exception():
    """Every agent turns this into its own kind of work — a task for a human,
    or a fall back to a template. None of them should have to catch it."""

    class Broken(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    result = await Harness(
        config=CONFIG, instructions="do the thing", model=Broken()
    ).run("go")

    assert result is None


async def test_the_reason_it_failed_is_kept():
    class Broken(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("provider down")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(config=CONFIG, instructions="do the thing", model=Broken())
    await run.run("go")

    assert "provider down" in run.last_error


async def test_a_credential_never_appears_in_the_reason():
    """The reason is stored against a task, and a provider exception can quote
    an Authorization header."""

    class Leaking(Model):
        async def get_response(self, *a, **kw):
            raise RuntimeError("401 for Bearer sk-abcdefghijklmnopqrstuvwx")

        def stream_response(self, *a, **kw):
            raise NotImplementedError

    run = Harness(config=CONFIG, instructions="i", model=Leaking())
    await run.run("go")

    assert "sk-abcdefghijklmnopqrstuvwx" not in run.last_error
    assert "REDACTED" in run.last_error


async def test_both_sides_of_the_call_are_collected():
    calls: list = []

    await harness([assistant_message("done")]).run("classify this", calls=calls)

    (call,) = calls
    assert call.agent == "an-agent"
    assert call.model == "test-model"
    assert "classify this" in call.prompt


async def test_configuration_reaches_the_agent():
    """`base_url`, `api_key` and `model` are the whole reason for this shape —
    a different OpenAI-compatible provider must need no code change."""
    built = Harness(config=CONFIG, instructions="do the thing")

    assert isinstance(built.agent, Agent)
    assert built.agent.model.model == "test-model"
    assert built.agent.model_settings.temperature == 0


async def test_tracing_is_off():
    """It exports to OpenAI using the same key as model requests, which with a
    third-party provider leaks both the traffic and the credential."""
    from agents.tracing import get_trace_provider

    Harness(config=CONFIG, instructions="i")

    assert get_trace_provider()._disabled


async def test_an_agent_can_be_given_servers_it_did_not_have_to_know_about():
    """Which servers an agent gets is composition, not something it declares —
    the same reason its model and base_url are configuration."""
    from friday.config import MCPServerConfig
    from friday.agent.mcp import build

    servers = build([MCPServerConfig(name="loki", command="npx", allow=("q",))])

    run = Harness(config=CONFIG, instructions="i", mcp_servers=servers)

    assert [s.name for s in run.agent.mcp_servers] == ["loki"]


async def test_this_is_the_only_module_that_imports_the_sdk():
    """Ticket 23's guarantee. Replacing the SDK is a rewrite of this file, and
    that is only true while nothing else reaches past it — declaring a tool
    pulls the library in, so a second importer would spread the dependency to
    every agent written after it."""
    import subprocess

    allowed = {"friday/agent/harness.py"}
    hits = subprocess.run(
        ["grep", "-rlE", r"^\s*(from agents|import agents)\b", "friday/"],
        capture_output=True, text=True,
    ).stdout.split()

    assert set(hits) <= allowed, f"unexpected importer: {set(hits) - allowed}"


async def test_run_accepts_a_rendered_section():
    """Ticket 27 widens the seam: a bundle's rendered string is the prompt,
    and nothing else about the call changes."""
    from agents.testing import ScriptedModel, assistant_message
    from friday.agent.instruction_prompt import task

    h = harness([assistant_message("done")])
    result = await h.run(task("classify", None, None).render())
    assert result.final_output == "done"


async def test_run_still_accepts_a_plain_string():
    """The scripted-test seam: a plain string keeps working unchanged."""
    from agents.testing import ScriptedModel, assistant_message

    h = harness([assistant_message("done")])
    result = await h.run("plain prompt")
    assert result.final_output == "done"
