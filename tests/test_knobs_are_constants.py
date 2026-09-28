"""Every knob is a named constant beside its user, pinned here (board
`domains-plug-in`, tickets 07 and 17; build board `build-the-spine`, ticket 01).

A knob tunes how Friday behaves and is the same on every machine, so changing
one is a commit — and this file is what makes that commit visible. Only install
facts stay in `config.yaml`.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from friday.sdk.agent import AgentDeclaration

ROOT = Path(__file__).resolve().parent.parent


def test_the_attempts_are_core_constants():
    """An attempt is a repeat of the same failed thing, the same for every
    agent (ticket 17)."""
    from friday.kernel.harness.harness import (
        OUTPUT_CORRECTIONS,
        PROVIDER_ATTEMPTS,
        PROVIDER_BACKOFF_SECONDS,
    )
    from friday.kernel.outbox import OUTBOX_ATTEMPTS, OUTBOX_BACKOFF_SECONDS

    assert (PROVIDER_ATTEMPTS, PROVIDER_BACKOFF_SECONDS) == (10, 10.0)
    assert OUTPUT_CORRECTIONS == 1
    assert (OUTBOX_ATTEMPTS, OUTBOX_BACKOFF_SECONDS) == (3, 30.0)


def test_each_agent_declares_its_tier_and_budget():
    """`(max_turns, tokens)` is the whole per-agent budget; `max_turns` counts
    tool turns, so each is what the agent had when tool turns were added on
    top. Changing triage's needs `run_triage_eval` first."""
    from friday.kernel.extraction import EXTRACTOR
    from friday.kernel.memory.channel_context import room_summary
    from friday.kernel.responder import RESPONDER
    from friday.kernel.triage import TRIAGE
    from plugins.devops.graph import DIAGNOSE

    assert TRIAGE == AgentDeclaration("triage", "flash", 0.0, 1, 50_000, 30.0)
    assert RESPONDER == AgentDeclaration("responder", "flash", 0.7, 13, 300_000, 60.0)
    assert EXTRACTOR == AgentDeclaration("extractor", "flash", 0.0, 3, 100_000, 30.0)
    assert DIAGNOSE == AgentDeclaration(
        "devops.diagnose", "flash", 0.0, 1, 500_000, 60.0
    )
    assert room_summary("flash") == AgentDeclaration(
        "summary", "flash", 0.0, 1, 100_000, 30.0
    )


def test_rooms_are_not_summarised_until_a_tier_is_named():
    """As shipped: no summary tier, so no summaries (fog review, 2026-09-28)."""
    from friday.kernel.memory.channel_context import ROOM_SUMMARY_TIER, SUMMARY_MAX_CHARS

    assert ROOM_SUMMARY_TIER is None
    assert SUMMARY_MAX_CHARS == 6000


def test_triage_and_responder_knobs():
    from friday.kernel.inbox import MAX_MESSAGE_AGE_SECONDS
    from friday.kernel.responder import TONE_EXAMPLES
    from friday.kernel.triage.runner import CONFIDENCE_THRESHOLD, EXAMPLES

    assert CONFIDENCE_THRESHOLD == 0.7
    assert EXAMPLES == 8
    assert MAX_MESSAGE_AGE_SECONDS == 24 * 3600
    assert TONE_EXAMPLES == 8


def test_ingest_and_ops_knobs():
    from friday.kernel.inbox import CONTEXT_MESSAGES, SWEEP_INTERVAL_SECONDS, TURN_SECONDS
    from friday.kernel.ops.backup import KEEP_BACKUPS
    from friday.kernel.ops.liveness import (
        DOWN_AFTER_SECONDS,
        HEARTBEAT_SECONDS,
        KEEP_MODEL_CALLS_DAYS,
        SUMMARY_AT_HOUR,
    )

    assert (TURN_SECONDS, SWEEP_INTERVAL_SECONDS, CONTEXT_MESSAGES) == (12.0, 300.0, 20)
    assert (HEARTBEAT_SECONDS, DOWN_AFTER_SECONDS) == (60.0, 300.0)
    assert (SUMMARY_AT_HOUR, KEEP_MODEL_CALLS_DAYS, KEEP_BACKUPS) == (9, 14.0, 7)


def test_the_only_time_limit_is_on_a_tool_call():
    from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS

    assert TOOL_CALL_TIMEOUT_SECONDS == 30.0


# --- no time budget left (ticket 17) -----------------------------------------


#: The names a time budget or a daily ceiling went by. `timeout_seconds=` as a
#: DBOS keyword argument is the library's own API, not a budget of ours, and
#: `request_timeout_seconds` bounds one model request, not a run.
_GONE = ("check_node_clocks", "check_graph_clocks", "context_window",
         "daily_token_budget", "NODE_CLOCK_MARGIN_SECONDS")


def test_no_time_budget_is_left_in_the_code():
    offenders = []
    for base in ("friday", "plugins"):
        for path in (ROOT / base).rglob("*.py"):
            text = path.read_text()
            for name in _GONE:
                if name in text:
                    offenders.append(f"{path.relative_to(ROOT)}: {name}")
            for line in text.splitlines():
                if re.search(r"\btimeout_seconds\b", line) and "DBOS." not in line:
                    offenders.append(f"{path.relative_to(ROOT)}: {line.strip()}")
    assert offenders == []


def test_the_shipped_config_holds_only_tiers_and_install_facts():
    """`config.yaml` = provider keys + named model tiers + install facts. The
    three keys still read until ticket 16 deletes their readers — `workflows`'
    `max_asks`/`use_responder`/`auto_ask_for_details`, `triage_examples` until
    ticket 13's assembled prompt — are listed so the list is complete."""
    import yaml

    raw = yaml.safe_load((ROOT / "config.yaml").read_text())

    assert set(raw) <= {
        "database_path", "tiers", "devops", "operator_id", "board_host",
        "board_port", "board_origins", "repo_root", "backup_dir", "mcp_servers",
        "workflows", "ingest", "context", "sensitive_words", "triage_examples",
    }
    assert set(raw["ingest"]) == {"mention_types", "watched_channels"}
    assert set(raw["context"]) == {"skills_directory"}
    assert set(raw["workflows"]) <= {
        "concurrency", "max_asks", "use_responder", "auto_ask_for_details",
    }
    for tier in raw["tiers"].values():
        assert set(tier) <= {"api_key", "provider", "base_url", "model", "settings"}


# --- the per-tool-call timeout (ticket 17) ------------------------------------


async def test_a_hung_tool_call_is_cut_by_the_per_call_timeout(monkeypatch):
    """A tool server that never answers cannot hold a pool slot: the call is
    cut at `TOOL_CALL_TIMEOUT_SECONDS`, the one time limit left."""
    import friday.sdk.sources as sources

    monkeypatch.setattr(sources, "TOOL_CALL_TIMEOUT_SECONDS", 0.05)

    class Slow:
        """Answers — but only after the per-call limit has long passed, so a
        missing limit shows as an answer rather than as a hung test."""

        async def call_tool(self, tool, arguments):
            await asyncio.sleep(0.5)
            return "late"

    reads = sources.Reads(server=Slow(), allowed=frozenset({"loki_query_range"}))

    with pytest.raises(TimeoutError):
        await reads.call("loki_query_range", {})


async def test_a_hung_ssh_read_is_cut_by_the_per_call_timeout(monkeypatch):
    import plugins.devops.sources.logs as logs

    monkeypatch.setattr(logs, "TOOL_CALL_TIMEOUT_SECONDS", 0.05)

    class Slow:
        returncode = 0

        async def communicate(self):
            await asyncio.sleep(0.5)
            return b"late", b""

        def kill(self):
            pass

        async def wait(self):
            return -9

    async def spawn(*args, **kwargs):
        return Slow()

    monkeypatch.setattr(logs.asyncio, "create_subprocess_exec", spawn)

    with pytest.raises(TimeoutError):
        await logs.SshKubectlSource()._run("kubectl get pods")


def test_an_agent_calling_an_mcp_tool_directly_is_bounded_the_same_way():
    from friday.kernel.config import MCPServerConfig
    from friday.kernel.harness.mcp import build
    from friday.sdk.sources import TOOL_CALL_TIMEOUT_SECONDS

    (toolset,) = build(
        [MCPServerConfig(name="loki", url="https://example.invalid/mcp")],
        allowed=frozenset({"loki_query_range"}),
    )

    # Where Pydantic AI hands it to the MCP session, which waits this long for
    # each request's answer (its own default is five minutes).
    session = toolset.wrapped.client._session_kwargs
    assert session["read_timeout_seconds"] == TOOL_CALL_TIMEOUT_SECONDS
