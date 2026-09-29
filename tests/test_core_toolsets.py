"""The core toolsets (build-the-spine ticket 08): `core.shell` reads and only
reads, `core.workspace` never leaves `/tmp/friday/<task_id>/`, and one module
imports pydantic-ai-harness.

One test per refusal class of the read-command allowlist, each refused before
anything runs; a refusal is written to `audit_log`.
"""

from __future__ import annotations

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from friday.kernel.audit import AuditLog
from friday.kernel.toolsets import NotWired, core_toolsets, workspace
from friday.kernel.toolsets.shell import refusal, shell_tools
from friday.sdk.redact import clear_secret_values, register_secret_values
from friday.sdk.toolset import RunContext
from plugins.backend.toolsets.evidence import Evidence

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def root(tmp_path, monkeypatch) -> Path:
    """Every workspace in these tests lives under `tmp_path`."""
    monkeypatch.setattr(workspace, "WORKSPACE_ROOT", tmp_path / "friday")
    return tmp_path / "friday"


def _run(task_id: int = 7) -> RunContext:
    return RunContext(
        task_id=task_id, domain=None, evidence=Evidence(), mcp={},
        reported_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
    )


def _run_command(db, run=None, hosts=("local",)):
    (tool,) = shell_tools(run or _run(), hosts=hosts, audit=AuditLog(db))
    return tool.function


# ── the allowlist: one test per refusal class ──────────────────────────────


def test_a_command_off_the_list_is_refused():
    assert "not on the read-command allowlist" in refusal("rm -rf /tmp/x")


def test_kubectl_is_refused_outside_its_read_verbs():
    assert "kubectl reads only" in refusal("kubectl delete pod api-0")


@pytest.mark.parametrize("command", ["ls; rm x", "ls && rm x", "ls || rm x", "ls & rm x", "(ls)"])
def test_a_control_operator_is_refused(command):
    assert "only `|` between read commands" in refusal(command)


@pytest.mark.parametrize("command", ["cat a > b", "grep x < a", "cat a >> b"])
def test_a_redirection_is_refused(command):
    assert "only `|` between read commands" in refusal(command)


@pytest.mark.parametrize("command", ["cat $(which sh)", "cat `which sh`"])
def test_a_substitution_is_refused(command):
    assert "command substitution" in refusal(command)


async def test_a_quoted_operator_stays_an_argument(db, tmp_path):
    """`grep "|"` searches for a bar; it does not become a pipe."""
    log = tmp_path / "a.log"
    log.write_text("a | b\nplain\n")
    assert refusal(f'grep "|" {log}') is None
    got = await _run_command(db)(host="local", command=f'grep "|" {log}')
    assert "a | b" in got and "plain" not in got
    assert refusal('grep ";" x') is None


def test_a_pipe_is_refused_when_one_segment_is_off_the_list():
    assert "'xargs' is not on" in refusal("ls | xargs rm")
    assert refusal("kubectl get pods -n api | grep Crash | head -5") is None


@pytest.mark.parametrize(
    "command", ["find . -delete", "find . -exec rm {} +", "journalctl --vacuum-time=1s"]
)
def test_a_write_flag_is_refused(command):
    assert "is refused" in refusal(command)


@pytest.mark.parametrize("command", [
    "kubectl get pods --kubeconfig=/tmp/friday/7/k.yaml",
    "kubectl get pods --kubeconfig /tmp/friday/7/k.yaml",
    "kubectl get pods --server=https://elsewhere",
    "kubectl get pods -shttps://elsewhere",
    "kubectl logs api-0 --token abc",
    "kubectl get pods --cache-dir=/etc",
    "journalctl --cursor-file=/etc/x",
])
def test_a_credential_or_write_flag_is_refused(command):
    """`--kubeconfig` names a file whose `exec` entry runs any binary — and
    the workspace can write that file."""
    assert "is refused" in refusal(command)


@pytest.mark.parametrize("command", ["", "  ", "ls |", "| ls", "cat 'unclosed"])
def test_an_empty_or_unparseable_command_is_refused(command):
    assert refusal(command) is not None


# ── the tool ────────────────────────────────────────────────────────────────


async def test_a_refused_command_writes_an_audit_row_and_runs_nothing(db, tmp_path):
    marker = tmp_path / "marker"
    got = await _run_command(db)(host="local", command=f"ls; touch {marker}")

    assert got.startswith("refused:")
    assert not marker.exists()
    (entry,) = await db.audit_entries(event="shell_refused")
    assert entry.detail["task_id"] == 7
    assert entry.detail["toolset"] == "core.shell"
    assert entry.detail["host"] == "local"
    assert entry.detail["command"] == f"ls; touch {marker}"


async def test_an_undeclared_host_is_refused_and_audited(db):
    got = await _run_command(db, hosts=("local",))(host="prod", command="ls")

    assert "host 'prod' is not declared" in got
    (entry,) = await db.audit_entries(event="shell_refused")
    assert entry.detail["host"] == "prod"


async def test_output_is_redacted_and_enters_evidence(db, tmp_path):
    log = tmp_path / "app.log"
    log.write_text("ok start\nfailed with key s3cr3t-value\nok end\n")
    run = _run()
    register_secret_values(["s3cr3t-value"])
    try:
        got = await _run_command(db, run)(host="local", command=f"cat {log} | grep failed")
    finally:
        clear_secret_values()

    assert got.startswith("exit 0")
    assert "s3cr3t-value" not in got
    assert run.evidence.index == {"L1": "failed with key [REDACTED]"}
    assert "L1 | failed with key [REDACTED]" in got


async def test_an_argument_reaches_the_shell_as_a_literal(db, tmp_path):
    """Re-quoted before `sh -c`: a `$HOME` in an argument is not expanded,
    even in the double quotes a shell would expand it in."""
    log = tmp_path / "a.log"
    log.write_text("price $HOME\n")
    got = await _run_command(db)(host="local", command=f'grep "$HOME" {log}')
    assert "price $HOME" in got


async def test_save_to_writes_the_output_into_the_workspace(db, tmp_path, root):
    log = tmp_path / "big.log"
    log.write_text("".join(f"line {n}\n" for n in range(500)))
    run = _run()
    got = await _run_command(db, run)(host="local", command=f"cat {log}", save_to="out/big.txt")

    assert "500 lines" in got and "saved to out/big.txt" in got
    assert (root / "7" / "out" / "big.txt").read_text() == log.read_text()
    assert run.evidence.index == {}


async def test_a_timeout_kills_the_whole_pipeline(db, tmp_path, monkeypatch):
    """`tail -f | grep` past the timeout: no process of the pipeline survives."""
    import subprocess

    from friday.kernel.toolsets import shell

    monkeypatch.setattr(shell, "TIMEOUT_SECONDS", 0.5)
    log = tmp_path / "follow-me.log"
    log.write_text("x\n")
    # Bounded here too: a pipeline child left holding the pipe would otherwise
    # hang the test rather than fail it.
    got = await asyncio.wait_for(
        _run_command(db)(host="local", command=f"tail -f {log} | grep x"), 5
    )

    assert "stopped after" in got
    alive = subprocess.run(["pgrep", "-f", str(log)], capture_output=True, text=True)
    assert alive.stdout.strip() == ""


# ── the workspace ───────────────────────────────────────────────────────────


async def test_a_path_escaping_the_workspace_root_is_refused(tmp_path, root):
    outside = tmp_path / "outside.txt"
    outside.write_text("not yours")
    (files,) = workspace.workspace_tools(7)

    with pytest.raises(Exception, match="outside the root"):
        await files.read_file("../../outside.txt")
    with pytest.raises(Exception, match="outside the root"):
        await files.write_file("../escaped.txt", "x")
    assert not (root / "escaped.txt").exists()

    said = await workspace.save(7, "../../outside.txt", "overwritten")
    assert said.startswith("not saved")
    assert outside.read_text() == "not yours"


async def test_a_path_inside_the_workspace_is_read_and_written(root):
    (files,) = workspace.workspace_tools(7)
    await files.write_file("a.txt", "hello")
    assert "hello" in await files.read_file("a.txt")
    assert (root / "7" / "a.txt").read_text() == "hello"


# ── the toolsets as registered ─────────────────────────────────────────────


def test_the_core_toolsets_are_named_and_described():
    specs = {spec.name: spec for spec in core_toolsets()}
    assert set(specs) == {"core.memory", "core.skills", "core.shell", "core.workspace"}
    assert all(spec.description.strip() for spec in specs.values())


def test_a_core_toolset_built_without_its_dependency_says_which():
    shell = next(s for s in core_toolsets() if s.name == "core.shell")
    with pytest.raises(NotWired, match="core.shell was built without a store"):
        shell.factory(_run())


def test_the_workspace_toolset_reaches_the_agent_beside_its_tools(root):
    """A factory may hand back a whole Pydantic AI toolset; the harness puts it
    with the tool servers rather than binding it as a tool."""
    from friday.kernel.config import AgentConfig
    from friday.kernel.harness.harness import Harness

    (files,) = workspace.workspace_tools(7)
    harness = Harness(
        config=AgentConfig(name="t", api_key="k", base_url="http://x", model="m"),
        instructions="i",
        tools=[files],
    )
    assert files in harness.tool_servers
    assert harness.tools == []


# ── the guard ───────────────────────────────────────────────────────────────


def test_only_one_module_imports_pydantic_ai_harness():
    """The reuse-before-rewrite seam: one module per adopted library."""
    importers = set()
    for path in REPO.rglob("*.py"):
        relative = path.relative_to(REPO).as_posix()
        if relative.startswith((".venv/", "web/")) or "/node_modules/" in relative:
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            if any(n.split(".")[0] == "pydantic_ai_harness" for n in names):
                importers.add(relative)
    assert importers == {"friday/kernel/toolsets/workspace.py"}


def test_boot_registers_the_core_toolsets_under_core(monkeypatch):
    from friday.kernel.plugin_host import load_plugins

    monkeypatch.setattr("friday.kernel.plugin_host.configured_plugins", lambda config: [])
    loaded = load_plugins(SimpleNamespace(shell_hosts=("local",)))
    toolsets = loaded.registry.toolsets()
    assert {"core.shell", "core.workspace"} <= set(toolsets)
    assert loaded.registry.owner_of("core.shell").id == "core"
